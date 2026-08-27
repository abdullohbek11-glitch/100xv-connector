# -*- coding: utf-8 -*-
"""2-bosqich: qo'ng'iroqni CRM lead'iga bog'lash + chatterga log (info + audio).

`levelix.calls.call.ingest`ni kengaytiramiz: har upsertdan keyin CRM ishlovi —
raqam bo'yicha lead top/yarat, lead chatteriga bitta message post/yangilash.
"""
import logging
import mimetypes

from markupsafe import Markup, escape

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

try:
    import phonenumbers
except ImportError:  # phone_validation bog'liqligi buni ta'minlaydi
    phonenumbers = None


def normalize_number(raw, region):
    """Raqamni E.164'ga (`+998901112233`) keltiradi. Bo'lmasa — oxirgi 9 raqam."""
    if not raw:
        return False
    raw = str(raw).strip()
    if phonenumbers:
        try:
            num = phonenumbers.parse(raw, region or "UZ")
            if phonenumbers.is_valid_number(num):
                return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    digits = "".join(c for c in raw if c.isdigit())
    return digits[-9:] if len(digits) >= 7 else False


def _fmt_duration(seconds):
    seconds = int(seconds or 0)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


class LevelixCall(models.Model):
    _inherit = "levelix.calls.call"

    partner_id = fields.Many2one("res.partner", string="Contact", index=True, check_company=True)
    lead_id = fields.Many2one("crm.lead", string="Opportunity", index=True, check_company=True)
    operator_user_id = fields.Many2one("res.users", string="Operator")
    message_id = fields.Many2one("mail.message", string="Chatter Message", copy=False)

    # ---------- Ingest hook ----------

    def ingest(self, payload, event_type, config):
        rec = super().ingest(payload, event_type, config)
        if rec:
            try:
                rec.sudo()._process_crm(event_type)
            except Exception as e:  # noqa: BLE001 — CRM xatosi eventni buzmasin
                _logger.exception("Levelix CRM processing failed: %s", e)
        return rec

    def _chatter_lang(self):
        """Chatter body qaysi tilda render bo'ladi. Webhook ingest context'i admin/en_US
        bo'lgani uchun aniq belgilaymiz — kompaniya (partner) tili, aks holda en_US."""
        return (self.company_id.partner_id.lang
                or self.env.company.partner_id.lang
                or "en_US")

    # ---------- CRM ishlovi ----------

    def _region(self):
        return (self.company_id.country_id.code or self.env.company.country_id.code or "UZ")

    def _company_domain(self):
        """Multi-company matching domeni: shared (company_id=False) YOKI shu qo'ng'iroq
        kompaniyasi. `sudo()` record rule'larni bypass qilgani uchun ZARUR (A.7.1)."""
        cid = (self.company_id or self.env.company).id
        return ["|", ("company_id", "=", False), ("company_id", "=", cid)]

    def _process_crm(self, event_type):
        self.ensure_one()
        # Multi-company: matching/create O'Z kompaniyasi kontekstida bajariladi.
        if self.company_id:
            self = self.with_company(self.company_id)
        # 1. Operator moslash — EMAIL bo'yicha (user-friendly). Lead yaratishdan OLDIN,
        #    aks holda yangi yaratilgan lead operatorga biriktirilmay qoladi.
        if not self.operator_user_id and self.backend_email:
            self.operator_user_id = self._match_operator(self.backend_email)
        # 2. Lead bog'lash (bir marta) — yangi lead operator user_id'siga biriktiriladi
        if not self.lead_id:
            self._link_lead()
        # 3. Yaratilgan/topilgan lead egasiz bo'lsa — operatorga biriktiramiz
        if self.lead_id and self.operator_user_id and not self.lead_id.user_id:
            self.lead_id.user_id = self.operator_user_id
        # 4. Chatter (lead bo'lsa) — kompaniya tilida render (webhook context'i en bo'lgani uchun).
        #    Har ingest'da qayta ishlaydi (kech-audio uchun) — shu bois "processed" flag YO'Q.
        if self.lead_id:
            self.with_context(lang=self._chatter_lang())._sync_chatter()

    def _match_operator(self, email):
        """Operatorni email bo'yicha topadi: 1) Odoo user login/email (AVTO),
        2) mapping jadvali (backend_email → user). Topilmasa bo'sh."""
        if not email:
            return False
        cid = (self.company_id or self.env.company).id
        # 1) Avto: Odoo user login/email shu bo'lsa VA shu kompaniyaga a'zo bo'lsa
        user = self.env["res.users"].search(
            [("company_ids", "in", cid),
             "|", ("login", "=ilike", email), ("email", "=ilike", email)],
            limit=1,
        )
        if user:
            return user
        # 2) Mapping jadvali (admin qo'lda moslagan istisnolar) — company bo'yicha
        m = self.env["levelix.calls.user.map"].search(
            self._company_domain() + [("backend_email", "=ilike", email), ("user_id", "!=", False)],
            limit=1,
        )
        return m.user_id if m else False

    def _link_lead(self):
        region = self._region()
        e164 = normalize_number(self.number_e164 or self.number, region)
        if not e164:
            return
        partner = self._find_partner(e164, region)
        lead = self._find_lead(partner, e164, region)
        if not lead and (not self.config_id or self.config_id.lead_not_found != "skip"):
            lead = self._create_lead(partner, e164)
        self.partner_id = partner or False
        self.lead_id = lead or False

    def _find_partner(self, e164, region):
        dom = self._company_domain()
        # phone_sanitized — Odoo o'zi E.164'ga normallashtirib saqlaydi (eng ishonchli)
        p = self.env["res.partner"].search(dom + [("phone_sanitized", "=", e164)], limit=1)
        if p:
            return p
        # Fallback: xom phone bo'yicha (baza tuzatilmagan bo'lsa)
        suffix = "".join(c for c in e164 if c.isdigit())[-9:]
        if not suffix:
            return False
        for cand in self.env["res.partner"].search(dom + [("phone", "ilike", suffix)], limit=30):
            if normalize_number(cand.phone, region) == e164:
                return cand
        return False

    def _find_lead(self, partner, e164, region):
        Lead = self.env["crm.lead"]
        dom = self._company_domain()
        if partner:
            lead = Lead.search(dom + [("partner_id", "=", partner.id)], order="write_date desc", limit=1)
            if lead:
                return lead
        lead = Lead.search(dom + [("phone_sanitized", "=", e164)], order="write_date desc", limit=1)
        if lead:
            return lead
        suffix = "".join(c for c in e164 if c.isdigit())[-9:]
        for cand in Lead.search(dom + [("phone", "ilike", suffix)], order="write_date desc", limit=30):
            if normalize_number(cand.phone, region) == e164:
                return cand
        return False

    def _create_lead(self, partner, e164):
        # P2.3: saqlanadigan raqam manba qiymati (number_e164/number) — normalize_number'ning
        # lossy 9-raqamli fallback'i EMAS (collision/noto'g'ri yozuvning oldi olinadi).
        phone = self.number_e164 or self.number or e164
        name = self.contact_name or (partner and partner.name) or (_("Call %s") % phone)
        vals = {
            "name": name,
            "phone": phone,
            "contact_name": self.contact_name or False,
            "type": "opportunity",  # pipeline'da ko'rinadigan imkoniyat (lead emas)
            "description": _("Auto-created from an XCall call."),
        }
        if partner:
            vals["partner_id"] = partner.id
        # Multi-company: lead ALBATTA qo'ng'iroq kompaniyasiga tushsin (sudo default emas).
        company = self.company_id or self.env.company
        vals["company_id"] = company.id
        # user_id'ni ALBATTA aniq belgilaymiz: operator bo'lsa unga, bo'lmasa egasiz (False).
        # Aks holda crm.lead default egasi (ingest = admin) qo'yiladi va keyin call.finished
        # operatorni biriktira olmaydi (lead "egali" deb hisoblanadi).
        vals["user_id"] = self.operator_user_id.id if self.operator_user_id else False
        return self.env["crm.lead"].with_company(company).create(vals)

    # ---------- Chatter ----------

    def _direction_label(self):
        return {"incoming": _("Incoming"), "outgoing": _("Outgoing")}.get(self.direction, _("Call"))

    def _status_label(self):
        """Backend status kodini (answered/missed/cancelled) tarjimali label'ga o'giradi."""
        return {
            "answered": _("Answered"),
            "missed": _("Missed"),
            "cancelled": _("Cancelled"),
        }.get(self.status, self.status or "")

    def _msg_subject(self):
        return "%s · %s" % (self._direction_label(), self.number_e164 or self.number or "")

    def _render_body(self, att=None):
        """Chatter body: asosiy info + chiziqli audio player + ochiluvchi tafsilotlar.

        Xom `str` qaytaradi — body SQL orqali yoziladi (sanitize `<audio controls>`ni
        o'chirmasin uchun), shuning uchun foydalanuvchi ma'lumotlari ESCAPE qilinadi.
        """
        def esc(v):
            return str(escape(v if v not in (None, False, "") else "—"))

        arrow = "⬇" if self.direction == "incoming" else "⬆" if self.direction == "outgoing" else "•"
        head = "%s %s · %s · %s · %s" % (
            arrow, esc(self._direction_label()),
            esc(self.number_e164 or self.number), esc(self._status_label()), _fmt_duration(self.duration),
        )
        when = esc(fields.Datetime.to_string(self.start_time)) if self.start_time else ""

        # Chiziqli HTML5 player (attachment /web/content orqali)
        audio = ""
        if att:
            audio = ('<audio controls preload="metadata" style="width:100%%;max-width:320px;'
                     'margin:6px 0" src="/web/content/%s?download=false"></audio>') % att.id

        # Operator: saqlangan qiymat yoki (bo'sh bo'lsa) mapping'dan jonli aniqlash —
        # mapping call'dan keyin qo'shilgan bo'lsa ham chatterda ko'rinsin.
        operator = self.operator_user_id or (
            self._match_operator(self.backend_email) if self.backend_email else False
        )
        rows = [
            (_("Operator"), operator.name if operator else None),
            (_("Started"), fields.Datetime.to_string(self.start_time)),
            (_("Answered"), fields.Datetime.to_string(self.answer_time)),
            (_("Ended"), fields.Datetime.to_string(self.end_time)),
            (_("Call UID"), self.call_uid),
        ]
        details = "".join("<li><b>%s:</b> %s</li>" % (esc(k), esc(v)) for k, v in rows)

        return (
            '<div>'
            '<div style="font-size:14px"><b>%s</b></div>'
            '<div style="color:#888;font-size:12px">%s</div>'
            '%s'
            '<details style="margin-top:6px"><summary style="cursor:pointer;color:#888">%s</summary>'
            '<ul style="margin:4px 0 0 0">%s</ul></details>'
            '</div>'
        ) % (head, when, audio, esc(_("Details")), details)

    def _ensure_audio_attachment(self):
        """Audio faylni LEAD'ga attachment qiladi (/web/content huquqi uchun; message
        kartochka sifatida ko'rinmasin). Player esa body'dagi <audio> orqali."""
        self.ensure_one()
        if not self.recording or not self.lead_id:
            return None
        name = self.recording_filename or ("call_%s.opus" % self.id)
        Att = self.env["ir.attachment"]
        att = Att.search([
            ("res_model", "=", "crm.lead"), ("res_id", "=", self.lead_id.id), ("name", "=", name),
        ], limit=1)
        if att:
            return att
        mime = mimetypes.guess_type(name)[0] or "audio/ogg"
        return Att.create({
            "name": name, "datas": self.recording,
            "res_model": "crm.lead", "res_id": self.lead_id.id, "mimetype": mime,
        })

    def _write_body_raw(self, html):
        """Body'ni SQL orqali yozadi — ORM sanitize `<audio controls>`ni o'chiradi."""
        self.env.cr.execute(
            "UPDATE mail_message SET body = %s WHERE id = %s", (html, self.message_id.id)
        )
        self.message_id.invalidate_recordset(["body"])

    def _sync_chatter(self):
        """Bitta message post yoki (kech audio) yangilash — body SQL orqali (player uchun)."""
        self.ensure_one()
        if not self.message_id:
            msg = self.lead_id.message_post(
                subject=self._msg_subject(),
                body=Markup(self._render_body()),  # dastlabki (audiosiz)
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            self.message_id = msg
        att = self._ensure_audio_attachment() if self.recording else None
        self._write_body_raw(self._render_body(att))
