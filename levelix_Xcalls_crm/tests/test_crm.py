# -*- coding: utf-8 -*-
"""CRM testlari: operator moslash, lead yaratish+biriktirish, status label,
live event chatter note'lari, to'liq oqim (live + finished)."""
import base64

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCrm(TransactionCase):

    def setUp(self):
        super().setUp()
        # Company uchun config unique — mavjud bo'lsa qayta ishlatamiz (rollback bo'ladi).
        Config = self.env["levelix.calls.config"]
        self.config = Config.search([("company_id", "=", self.env.company.id)], limit=1)
        if not self.config:
            self.config = Config.create({
                "name": "Test CRM",
                "company_id": self.env.company.id,
                "webhook_secret": "secret",
                "endpoint_token": "tok2",
            })
        self.Call = self.env["levelix.calls.call"]
        self.Lead = self.env["crm.lead"]
        self.op = self.env["res.users"].create({
            "name": "Operator One",
            "login": "operator1@test.com",
            "email": "operator1@test.com",
        })

    def _finished(self, uid, number, email=None, status="answered"):
        return {"call": {
            "uid": uid, "direction": "incoming", "number": number,
            "number_e164": number, "status": status, "duration": 12,
            "user_email": email,
        }}

    def _lead_body(self, lead):
        return " ".join(lead.message_ids.mapped(lambda m: m.body or ""))

    # ---------- operator moslash ----------

    def test_operator_match_by_login(self):
        rec = self.Call.ingest(self._finished("c1", "+998911111111", email="operator1@test.com"), "call.finished", self.config)
        self.assertEqual(rec.operator_user_id, self.op)

    def test_operator_match_by_mapping_table(self):
        self.env["levelix.calls.user.map"].create({
            "backend_email": "backend-only@x.com",
            "user_id": self.op.id,
            "company_id": self.env.company.id,
        })
        rec = self.Call.ingest(self._finished("c2", "+998922222222", email="backend-only@x.com"), "call.finished", self.config)
        self.assertEqual(rec.operator_user_id, self.op)

    def test_operator_none_when_no_match(self):
        rec = self.Call.ingest(self._finished("c3", "+998933333333", email="nobody@x.com"), "call.finished", self.config)
        self.assertFalse(rec.operator_user_id)

    # ---------- lead yaratish + biriktirish ----------

    def test_lead_created_and_assigned_to_operator(self):
        rec = self.Call.ingest(self._finished("c4", "+998944444444", email="operator1@test.com"), "call.finished", self.config)
        self.assertTrue(rec.lead_id, "lead yaratilishi kerak")
        self.assertEqual(rec.lead_id.type, "opportunity")
        self.assertEqual(rec.lead_id.user_id, self.op, "lead operatorga biriktirilishi kerak")

    def test_existing_ownerless_lead_gets_operator(self):
        lead = self.Lead.create({"name": "Existing", "type": "opportunity",
                                  "phone": "+998955555555", "user_id": False})
        rec = self.Call.ingest(self._finished("c5", "+998955555555", email="operator1@test.com"), "call.finished", self.config)
        self.assertEqual(rec.lead_id.id, lead.id, "mavjud lead topilishi kerak")
        self.assertEqual(rec.lead_id.user_id, self.op)

    def test_existing_owned_lead_not_reassigned(self):
        other = self.env["res.users"].create({"name": "Other", "login": "other@test.com"})
        lead = self.Lead.create({"name": "Owned", "type": "opportunity",
                                 "phone": "+998966666666", "user_id": other.id})
        self.Call.ingest(self._finished("c6", "+998966666666", email="operator1@test.com"), "call.finished", self.config)
        self.assertEqual(lead.user_id, other, "mavjud egasi buzilmasligi kerak")

    # ---------- status label (tarjimali) ----------

    def test_status_label_maps(self):
        rec = self.Call.ingest(self._finished("c7", "+998977777777", status="missed"), "call.finished", self.config)
        self.assertEqual(rec._status_label(), "Missed")
        rec.status = "cancelled"
        self.assertEqual(rec._status_label(), "Cancelled")
        rec.status = "answered"
        self.assertEqual(rec._status_label(), "Answered")
        rec.status = "weird_unknown"
        self.assertEqual(rec._status_label(), "weird_unknown")  # fallback: xom

    # ---------- chatter (full message) ----------

    def test_chatter_message_posted_with_status_label(self):
        rec = self.Call.ingest(self._finished("c8", "+998988888888", email="operator1@test.com", status="answered"), "call.finished", self.config)
        self.assertTrue(rec.message_id)
        # Chatter kompaniya tilida render bo'ladi (tarjima yoqilsa status ham tarjimalanadi),
        # shu bois tildan mustaqil belgilarni tekshiramiz: operator ismi + raqam.
        body = self._lead_body(rec.lead_id)
        self.assertIn("Operator One", body)
        self.assertIn("+998988888888", body)

    # ---------- multi-company matching (P0.2 / P0.3) ----------

    def test_cross_company_partner_not_matched(self):
        """A webhooki B kompaniya kontaktiga BOG'LANMASLIGI kerak (sudo domensiz emas)."""
        company_b = self.env["res.company"].create({"name": "CRM CompB"})
        p_b = self.env["res.partner"].create({
            "name": "B contact", "phone": "+998900000001", "company_id": company_b.id})
        rec = self.Call.ingest(self._finished("mc1", "+998900000001"), "call.finished", self.config)
        self.assertNotEqual(rec.partner_id, p_b, "boshqa kompaniya kontakti ilinmasligi kerak")

    def test_cross_company_operator_not_matched(self):
        company_b = self.env["res.company"].create({"name": "CRM CompB2"})
        self.env["res.users"].create({
            "name": "Op B", "login": "opb@crm.test", "email": "opb@crm.test",
            "company_id": company_b.id, "company_ids": [(6, 0, [company_b.id])]})
        rec = self.Call.ingest(self._finished("mc2", "+998900000002", email="opb@crm.test"),
                               "call.finished", self.config)
        self.assertFalse(rec.operator_user_id, "boshqa kompaniya operatori moslanmasligi kerak")

    def test_created_lead_gets_call_company(self):
        # ASL bugni ushlash uchun: config B kompaniyasida, env.company esa A (o'zgarmagan).
        company_b = self.env["res.company"].create({"name": "Lead CompB"})
        config_b = self.env["levelix.calls.config"].create({
            "name": "CfgB", "company_id": company_b.id,
            "webhook_secret": "s", "endpoint_token": "lead-b-tok"})
        self.assertNotEqual(company_b, self.env.company)
        rec = self.Call.ingest(self._finished("mc3", "+998900000003"), "call.finished", config_b)
        self.assertTrue(rec.lead_id)
        self.assertEqual(rec.lead_id.company_id, company_b,
                         "lead config kompaniyasiga tushishi kerak (env.company emas)")

    def test_user_map_user_must_be_company_member(self):
        company_b = self.env["res.company"].create({"name": "Map CompB"})
        user_b = self.env["res.users"].create({
            "name": "UB", "login": "ub@map.test",
            "company_id": company_b.id, "company_ids": [(6, 0, [company_b.id])]})
        with self.assertRaises(ValidationError):
            self.env["levelix.calls.user.map"].create({
                "backend_email": "x@map.test", "user_id": user_b.id,
                "company_id": self.env.company.id})  # A company + B user → rad

    # ---------- kech audio (P2.4) ----------

    def test_late_audio_attaches_to_same_message(self):
        rec = self.Call.ingest(self._finished("la1", "+998900000009", email="operator1@test.com"),
                               "call.finished", self.config)
        msg = rec.message_id
        self.assertTrue(msg)
        rec.recording = base64.b64encode(b"FAKEAUDIO")
        rec.recording_filename = "la1.opus"
        rec._process_crm("recording.ready")
        self.assertEqual(rec.message_id, msg, "o'sha message yangilanishi kerak")
        att = self.env["ir.attachment"].search([
            ("res_model", "=", "crm.lead"), ("res_id", "=", rec.lead_id.id),
            ("name", "=", "la1.opus")])
        self.assertTrue(att, "audio attachment paydo bo'lishi kerak")
        self.assertIn("<audio", rec.message_id.body)
