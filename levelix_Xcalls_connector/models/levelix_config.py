# -*- coding: utf-8 -*-
"""Levelix Calls connection (config) model.

One record per `res.company` (per-company integration):
- `api_key` — integration key from the XCall panel (X-API-Key).
- `webhook_secret` — AUTO-generated on the Odoo side (shared HMAC secret).
- `endpoint_token` — random token in the receiver URL (routing + non-guessable).

The buttons call the Levelix backend webhook management endpoints.
"""
import logging
import secrets

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

REQ_TIMEOUT = 10  # seconds


class LevelixCallsConfig(models.Model):
    _name = "levelix.calls.config"
    _description = "XCall Connection"
    _rec_name = "name"

    name = fields.Char(default="XCall", required=True)
    base_url = fields.Char(
        string="Backend URL",
        help="XCall server base, e.g. https://api.levelix.uz",
    )
    api_key = fields.Char(string="API Key", copy=False)
    webhook_secret = fields.Char(string="Webhook Secret", readonly=True, copy=False)
    endpoint_token = fields.Char(string="Endpoint Token", readonly=True, copy=False, index=True)
    webhook_url = fields.Char(
        string="Webhook URL", compute="_compute_webhook_url",
        help="This URL is registered on the server on subscribe. The server posts events here.",
    )
    subscription_id = fields.Integer(string="Subscription ID", readonly=True, copy=False)
    subscription_event = fields.Selection(
        selection=[
            ("*", "All (*)"),
            ("call.finished", "call.finished"),
            ("recording.ready", "recording.ready"),
        ],
        string="Subscribed Event",
        default="*",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("connected", "Connected"),
        ],
        default="draft",
        readonly=True,
        copy=False,
    )
    last_error = fields.Char(string="Last Error", readonly=True, copy=False)
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company, required=True, index=True,
    )
    active = fields.Boolean(default=True)

    _uniq_company = models.Constraint(
        "unique(company_id)", "Only one connection per company is allowed."
    )
    # Webhook routing token noyob bo'lishi SHART — aks holda 2 config bir xil token
    # olib, controller (limit=1) noto'g'ri kompaniyani tanlab qo'yadi (A.7.5).
    _uniq_endpoint_token = models.Constraint(
        "unique(endpoint_token)", "This endpoint token is already in use."
    )

    # ---------- Validatsiya ----------

    @api.constrains("webhook_secret")
    def _check_webhook_secret(self):
        """Bo'sh secret HMAC'ni buzadi (empty-key imzo qalbakilashtiriladi) — taqiqlanadi."""
        for rec in self:
            if not rec.webhook_secret:
                raise ValidationError(_("Webhook secret cannot be empty."))

    # ---------- Auto-generation ----------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("webhook_secret"):
                vals["webhook_secret"] = secrets.token_urlsafe(24)
            if not vals.get("endpoint_token"):
                vals["endpoint_token"] = secrets.token_urlsafe(16)
        return super().create(vals_list)

    def _compute_webhook_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        base = base.rstrip("/")
        for rec in self:
            rec.webhook_url = (
                "%s/levelix/calls/webhook/%s" % (base, rec.endpoint_token)
                if rec.endpoint_token else False
            )

    # ---------- Singleton ochish (menyu → to'g'ridan form) ----------

    @api.model
    def action_open_connection(self):
        """Joriy company uchun yagona ulanish yozuvini ochadi (bo'lmasa — yaratadi)."""
        rec = self.search([("company_id", "=", self.env.company.id)], limit=1)
        if not rec:
            rec = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Connection"),
            "res_model": "levelix.calls.config",
            "view_mode": "form",
            "res_id": rec.id,
            "target": "current",
        }

    # ---------- Helpers ----------

    def _clean_base(self):
        self.ensure_one()
        if not self.base_url:
            raise UserError(_("Backend URL is not set."))
        return self.base_url.rstrip("/")

    def _headers(self):
        return {"X-API-Key": self.api_key or "", "Content-Type": "application/json"}

    @staticmethod
    def _notify(title, message, kind="success", reload=False):
        params = {"title": title, "message": message, "type": kind, "sticky": False}
        if reload:
            # Notification'dan keyin formani qayta o'qish — state/subscription_id UI'да
            # darhol yangilansin (aks holda qo'lda refresh kerak bo'ladi).
            params["next"] = {"type": "ir.actions.client", "tag": "soft_reload"}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": params,
        }

    # ---------- Buttons ----------

    def _remove_subscription(self, base):
        """Serverdagi mavjud obunani o'chiradi (qayta-obuna oldidan, idempotentlik)."""
        if self.subscription_id:
            try:
                requests.delete(
                    base + "/api/v1/webhooks/%s" % self.subscription_id,
                    headers=self._headers(), timeout=REQ_TIMEOUT,
                )
            except requests.RequestException:
                pass
        self.subscription_id = False

    def action_connect(self):
        """One step: verify the API key, then subscribe the Odoo webhook URL.

        Re-running re-subscribes (drops the old subscription first), so it doubles
        as "reconnect" after changing the URL or key.
        """
        self.ensure_one()
        base = self._clean_base()
        if not self.api_key:
            raise UserError(_("API Key is not set."))

        # 1) Verify the key (read-only check)
        try:
            resp = requests.get(base + "/api/v1/webhooks", headers=self._headers(), timeout=REQ_TIMEOUT)
        except requests.RequestException as e:
            self.write({"state": "draft", "last_error": str(e)[:250]})
            return self._notify(_("Connection error"), str(e)[:200], "danger", reload=True)
        if resp.status_code != 200:
            self.write({"state": "draft", "last_error": "HTTP %s" % resp.status_code})
            return self._notify(
                _("Error"), _("Invalid key or URL (HTTP %s).") % resp.status_code, "danger",
                reload=True,
            )

        # 2) Subscribe (idempotent: drop the old one first)
        self._remove_subscription(base)
        body = {
            "event": self.subscription_event or "*",
            "url": self.webhook_url,
            "secret": self.webhook_secret,
        }
        try:
            resp = requests.post(
                base + "/api/v1/webhooks", json=body, headers=self._headers(), timeout=REQ_TIMEOUT
            )
        except requests.RequestException as e:
            self.write({"state": "draft", "last_error": str(e)[:250]})
            return self._notify(_("Error"), str(e)[:200], "danger", reload=True)
        if resp.status_code == 200:
            data = resp.json()
            self.write({
                "subscription_id": data.get("id"), "state": "connected", "last_error": False,
            })
            return self._notify(
                _("Connected"), _("Connected — webhooks registered."), "success", reload=True
            )
        self.write({
            "state": "draft",
            "last_error": "HTTP %s: %s" % (resp.status_code, resp.text[:150]),
        })
        return self._notify(
            _("Error"), _("Subscription failed (HTTP %s).") % resp.status_code, "danger",
            reload=True,
        )

    def action_send_test(self):
        """Ask the server to send a test event → it hits the Odoo receiver (round-trip)."""
        self.ensure_one()
        base = self._clean_base()
        if not self.subscription_id:
            raise UserError(_("Subscribe first."))
        try:
            resp = requests.post(
                base + "/api/v1/webhooks/%s/test" % self.subscription_id,
                headers=self._headers(), timeout=15,
            )
        except requests.RequestException as e:
            return self._notify(_("Error"), str(e)[:200], "danger")
        return self._notify(
            _("Test sent"),
            _("Server responded: HTTP %s. Check the Events Log.") % resp.status_code,
            "info",
        )
