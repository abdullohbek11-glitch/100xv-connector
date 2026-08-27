# -*- coding: utf-8 -*-
"""Connector ingest testlari: durable (call.finished/recording.ready) + live + webhook HMAC."""
import hashlib
import hmac
import json

from psycopg2 import IntegrityError

from odoo.tests.common import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestIngest(TransactionCase):

    def setUp(self):
        super().setUp()
        # Company uchun config unique — mavjud bo'lsa qayta ishlatamiz (rollback bo'ladi).
        Config = self.env["levelix.calls.config"]
        self.config = Config.search([("company_id", "=", self.env.company.id)], limit=1)
        if not self.config:
            self.config = Config.create({
                "name": "Test Connector",
                "company_id": self.env.company.id,
                "webhook_secret": "secret",
                "endpoint_token": "tok",
            })
        self.Call = self.env["levelix.calls.call"]

    def _finished(self, uid, **kw):
        call = {"uid": uid, "direction": "incoming", "number": "+998901112233"}
        call.update(kw)
        return {"call": call}

    # ---------- durable ----------

    def test_ingest_finished_creates_record(self):
        rec = self.Call.ingest(
            self._finished("u1", status="answered", duration=15), "call.finished", self.config
        )
        self.assertTrue(rec)
        self.assertEqual(rec.call_uid, "u1")
        self.assertEqual(rec.direction, "incoming")
        self.assertEqual(rec.status, "answered")
        self.assertEqual(rec.duration, 15)

    def test_ingest_no_uid_returns_false(self):
        self.assertFalse(self.Call.ingest({"call": {}}, "call.finished", self.config))

    def test_ingest_idempotent(self):
        p = self._finished("u2", status="missed")
        r1 = self.Call.ingest(p, "call.finished", self.config)
        r2 = self.Call.ingest(p, "call.finished", self.config)
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(self.Call.search_count([("call_uid", "=", "u2")]), 1)

    def test_bad_direction_becomes_false(self):
        rec = self.Call.ingest(self._finished("u3", direction="weird"), "call.finished", self.config)
        self.assertFalse(rec.direction)

    # ---------- multi-company izolyatsiya (P0.1 ir.rule) ----------

    def test_cross_company_call_read_isolated(self):
        """A kompaniya manageri B kompaniya qo'ng'irog'ini KO'RA OLMASLIGI kerak."""
        company_b = self.env["res.company"].create({"name": "XCall CompB"})
        self.Call.create({"call_uid": "cc-b", "company_id": company_b.id})
        user_a = self.env["res.users"].create({
            "name": "A Manager", "login": "a-mgr@xcall.test",
            "company_id": self.env.company.id,
            "company_ids": [(6, 0, [self.env.company.id])],
            "group_ids": [(4, self.env.ref("levelix_calls_connector.group_levelix_manager").id)],
        })
        found = self.Call.with_user(user_a).search([("call_uid", "=", "cc-b")])
        self.assertFalse(found, "boshqa kompaniya qo'ng'irog'i ko'rinmasligi kerak")

    def test_endpoint_token_unique(self):
        """Ikki config bir xil endpoint_token ololmaydi (webhook routing invarianti)."""
        company_b = self.env["res.company"].create({"name": "XCall TokB"})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self.env["levelix.calls.config"].create({
                "name": "dup", "company_id": company_b.id,
                "webhook_secret": "s", "endpoint_token": self.config.endpoint_token})
            self.env.flush_all()


@tagged("post_install", "-at_install")
class TestWebhookHttp(HttpCase):
    """Webhook controller: HMAC imzo (200/401) va noto'g'ri token (404)."""

    def test_webhook_signature_and_token(self):
        Config = self.env["levelix.calls.config"]
        cfg = Config.search([("company_id", "=", self.env.company.id)], limit=1)
        if not cfg:
            cfg = Config.create({"name": "http", "company_id": self.env.company.id})
        cfg.write({"webhook_secret": "sek-http", "endpoint_token": "htok-http"})
        self.env.flush_all()

        body = json.dumps(
            {"event": "call.finished", "call": {"uid": "http1", "direction": "incoming"}}
        ).encode()
        base_h = {"Content-Type": "application/json", "X-LC-Event": "call.finished"}

        # 1) noto'g'ri imzo → 401
        r = self.url_open("/levelix/calls/webhook/htok-http", data=body,
                          headers={**base_h, "X-LC-Signature": "sha256=bad"})
        self.assertEqual(r.status_code, 401)

        # 2) to'g'ri imzo → 200
        sig = "sha256=" + hmac.new(b"sek-http", body, hashlib.sha256).hexdigest()
        r = self.url_open("/levelix/calls/webhook/htok-http", data=body,
                          headers={**base_h, "X-LC-Signature": sig})
        self.assertEqual(r.status_code, 200)

        # 3) noto'g'ri token → 404
        r = self.url_open("/levelix/calls/webhook/nope-xyz", data=body,
                          headers={**base_h, "X-LC-Signature": sig})
        self.assertEqual(r.status_code, 404)
