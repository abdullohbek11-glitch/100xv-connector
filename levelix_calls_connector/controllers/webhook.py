# -*- coding: utf-8 -*-
"""Levelix Calls webhook qabul qiluvchi.

Levelix backend imzolangan POST yuboradi:
  X-LC-Signature: sha256=<HMAC-SHA256(secret, xom_body) hex>
  X-LC-Event-Id:  <uuid>   (durable eventlarda; dedup kaliti)
  X-LC-Event:     <event_type>

Qadamlar: token bo'yicha config top → HMAC tekshir → dedup → jurnalga yoz → 200.

MUHIM: imzo XOM bayt ustidan hisoblanadi, shuning uchun type='http' (JSON emas) —
body'ni o'zimiz o'qiymiz. Backend 2xx'ni muvaffaqiyat deb biladi; dublikat ham 200
qaytishi kerak (aks holda cheksiz retry).
"""
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class LevelixWebhookController(http.Controller):

    @http.route(
        "/levelix/calls/webhook/<string:token>",
        type="http", auth="public", methods=["POST"], csrf=False, save_session=False,
    )
    def receive(self, token, **kw):
        raw = request.httprequest.get_data()  # xom bayt (imzo shu ustidan)

        config = request.env["levelix.calls.config"].sudo().search(
            [("endpoint_token", "=", token)], limit=1
        )
        if not config:
            return Response(status=404)
        # Bo'sh secret bilan HMAC empty-key bo'lib qoladi (imzo qalbakilashtiriladi) — rad.
        if not config.webhook_secret:
            return Response(status=401)

        sig = request.httprequest.headers.get("X-LC-Signature") or ""
        event_id = request.httprequest.headers.get("X-LC-Event-Id") or ""
        event_type = request.httprequest.headers.get("X-LC-Event") or ""

        # ---- HMAC tekshiruv ----
        expected = "sha256=" + hmac.new(
            (config.webhook_secret or "").encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            _logger.warning("Levelix webhook: imzo mos kelmadi (event=%s)", event_type)
            return Response(status=401)

        Event = request.env["levelix.calls.event"].sudo()

        # ---- Dedup (idempotentlik) ----
        if event_id and Event.search_count([("event_uid", "=", event_id)]):
            return Response(status=200)  # allaqachon qabul qilingan → OK

        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            payload = {}
        call = payload.get("call") or {}

        etype = event_type or payload.get("event") or False
        Event.create({
            "config_id": config.id,
            "company_id": config.company_id.id,
            "event_uid": event_id or False,
            "event_type": etype,
            "call_uid": call.get("uid") or False,
            "payload": json.dumps(payload, ensure_ascii=False, indent=2),
            "signature_ok": True,
            "state": "received",
        })

        # Metadata/audio bo'lgan eventlarni Calls yozuviga upsert qilamiz (best-effort —
        # xato bo'lsa ham eventni ACK qilamiz, aks holda server qayta-qayta uradi).
        if etype in ("call.finished", "recording.ready"):
            try:
                request.env["levelix.calls.call"].sudo().ingest(payload, etype, config)
            except Exception as e:  # noqa: BLE001
                _logger.exception("Levelix: call ingest failed (%s): %s", etype, e)

        return Response(status=200)
