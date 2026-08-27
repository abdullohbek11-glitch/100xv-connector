# -*- coding: utf-8 -*-
"""Levelix Call — qo'ng'iroq yozuvi (metadata + audio).

Eventlar (`call.finished`, `recording.ready`) qabul qilinganda controller shu
modelga upsert qiladi: metadata `call.finished`dan, audio esa `recording.ready`dan
(presigned URL + best-effort yuklab olish).
"""
import base64
import ipaddress
import logging
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Audio yuklashda maksimal hajm (SSRF/resurs himoyasi) — worker xotirasini asraydi.
MAX_RECORDING_BYTES = 25 * 1024 * 1024  # 25 MB


def _parse_dt(value):
    """ISO-8601 (masalan '2026-07-23T10:19:00+00:00') → Odoo uchun naive UTC datetime."""
    if not value:
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return False


class LevelixCall(models.Model):
    _name = "levelix.calls.call"
    _description = "XCall Call"
    _order = "start_time desc, id desc"
    _rec_name = "call_uid"
    _check_company_auto = True

    call_uid = fields.Char(string="Call UID", index=True, required=True)
    direction = fields.Selection(
        selection=[("incoming", "Incoming"), ("outgoing", "Outgoing")],
        string="Direction",
    )
    number = fields.Char(string="Number")
    number_e164 = fields.Char(string="Number (E.164)")
    contact_name = fields.Char(string="Contact")
    backend_user_id = fields.Integer(string="Operator (backend id)")
    backend_email = fields.Char(string="Operator email", index=True)  # Odoo moslash kaliti
    backend_device_id = fields.Integer(string="Device (backend id)")
    start_time = fields.Datetime(string="Start")
    answer_time = fields.Datetime(string="Answered")
    end_time = fields.Datetime(string="End")
    duration = fields.Integer(string="Duration (s)")
    status = fields.Char(string="Status")

    recording_url = fields.Char(string="Recording URL")
    recording_format = fields.Char(string="Recording Format")
    recording = fields.Binary(string="Recording", attachment=True)
    recording_filename = fields.Char(string="Recording Filename")
    recording_player = fields.Html(string="Player", compute="_compute_player", sanitize=False)

    company_id = fields.Many2one("res.company", string="Company", index=True, required=True)
    config_id = fields.Many2one(
        "levelix.calls.config", string="Connection", ondelete="set null", check_company=True
    )

    _uniq_company_call = models.Constraint(
        "unique(company_id, call_uid)", "This call already exists."
    )

    @api.depends("recording", "recording_url")
    def _compute_player(self):
        for rec in self:
            src = None
            if rec.recording:
                src = ("/web/content?model=levelix.calls.call&id=%s"
                       "&field=recording&filename_field=recording_filename&download=false" % rec.id)
            elif rec.recording_url:
                src = rec.recording_url
            # `sanitize=False` maydon — src ESCAPE qilinadi (recording_url tashqi, ishonchsiz).
            rec.recording_player = (
                Markup('<audio controls preload="none" style="width:100%%" src="%s"></audio>')
                % escape(src)
            ) if src else False

    # ---------- Ingest (controllerdan chaqiriladi, sudo) ----------

    @api.model
    def ingest(self, payload, event_type, config):
        """Event payload → qo'ng'iroq yozuvini yaratadi/yangilaydi."""
        call = payload.get("call") or {}
        uid = call.get("uid")
        if not uid:
            return False

        company = config.company_id
        rec = self.search(
            [("company_id", "=", company.id), ("call_uid", "=", uid)], limit=1
        )
        direction = call.get("direction")
        if direction not in ("incoming", "outgoing"):
            direction = False
        vals = {
            "call_uid": uid,
            "direction": direction,
            "number": call.get("number"),
            "number_e164": call.get("number_e164"),
            "contact_name": call.get("contact_name"),
            "backend_user_id": call.get("user_id"),
            "backend_email": call.get("user_email"),
            "backend_device_id": call.get("device_id"),
            "start_time": _parse_dt(call.get("start_time")),
            "answer_time": _parse_dt(call.get("answer_time")),
            "end_time": _parse_dt(call.get("end_time")),
            "duration": call.get("duration"),
            "status": call.get("status"),
            "company_id": company.id,
            "config_id": config.id,
        }
        if rec:
            rec.write(vals)
        else:
            rec = self.create(vals)

        # Audio — recording.ready eventida
        if event_type == "recording.ready":
            recording = payload.get("recording") or {}
            url = recording.get("url")
            if url:
                rec.recording_url = url
                rec.recording_format = recording.get("format")
                rec.recording_filename = "%s.%s" % (uid, recording.get("format") or "audio")
                rec._download_recording(url)
        return rec

    def _recording_url_ok(self, url):
        """SSRF/resurs himoyasi. Ustuvorlik:
        1) `xcall.recording_allowed_hosts` (vergul bilan) o'rnatilgan bo'lsa — FAQAT shu hostlar;
        2) aks holda `https` + private/loopback/link-local IP RAD (`recording_allow_private=1`
           ichki storage uchun o'chiradi). (DNS-rebinding'ga to'liq qarshi emas — pragmatik.)"""
        try:
            p = urlparse(url or "")
        except ValueError:
            return False
        if p.scheme != "https" or not p.hostname:
            return False
        icp = self.env["ir.config_parameter"].sudo()
        allowed = [h.strip().lower() for h in
                   (icp.get_param("xcall.recording_allowed_hosts", "") or "").split(",") if h.strip()]
        if allowed:
            return p.hostname.lower() in allowed
        if icp.get_param("xcall.recording_allow_private", "0") == "1":
            return True
        try:
            infos = socket.getaddrinfo(p.hostname, None)
        except socket.gaierror:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True

    def _download_recording(self, url):
        """Audio faylni presigned URL orqali yuklab, binary'ga saqlaydi (best-effort).

        Himoya: `https`-only, host allowlist/private-rad (SSRF), **redirect'lar QO'LDA
        kuzatiladi va har biri so'rovdan OLDIN validatsiya qilinadi** (`allow_redirects=False`),
        streaming + hajm limiti (MAX_RECORDING_BYTES), Content-Type tekshiruvi.
        """
        self.ensure_one()
        max_redirects = 5
        for _hop in range(max_redirects + 1):
            # Har hop URL'i so'rovdan OLDIN tekshiriladi → private hostga request YUBORILMAYDI.
            if not self._recording_url_ok(url):
                _logger.warning("XCall: audio URL rad etildi (https/host siyosati)")
                return False
            try:
                resp = requests.get(url, timeout=15, stream=True, allow_redirects=False)
            except requests.RequestException as e:
                _logger.warning("XCall: audio yuklab bo'lmadi: %s", e)
                return False
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        return False
                    url = urljoin(url, loc)  # keyingi hop — sikl tepasida QAYTA validatsiya
                    continue
                if resp.status_code != 200:
                    _logger.warning("XCall: audio HTTP %s", resp.status_code)
                    return False
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ct and not (ct.startswith("audio/") or ct == "application/octet-stream"):
                    _logger.warning("XCall: kutilmagan audio Content-Type: %s", ct)
                    return False
                buf, total = [], 0
                for chunk in resp.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_RECORDING_BYTES:
                        _logger.warning("XCall: audio hajmi limitdan oshdi (%s bayt)", total)
                        return False
                    buf.append(chunk)
                self.recording = base64.b64encode(b"".join(buf))
                return True
            finally:
                resp.close()
        _logger.warning("XCall: audio redirect limiti oshib ketdi")
        return False
