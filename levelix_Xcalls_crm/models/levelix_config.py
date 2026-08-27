# -*- coding: utf-8 -*-
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class LevelixCallsConfig(models.Model):
    _inherit = "levelix.calls.config"

    lead_not_found = fields.Selection(
        selection=[
            ("create", "Create a new opportunity"),
            ("skip", "Skip (keep call only)"),
        ],
        string="When no match found",
        default="create",
        help="What to do when an incoming call number has no matching opportunity/lead.",
    )

    def action_fetch_operators(self):
        """Backend'dan operatorlar (email+ism) ro'yxatini tortib, mapping jadvalini
        to'ldiradi (yangi email'lar qo'shiladi; admin Odoo user'ini tanlaydi)."""
        self.ensure_one()
        base = self._clean_base()
        try:
            resp = requests.get(
                base + "/api/v1/operators", headers=self._headers(), timeout=15
            )
        except requests.RequestException as e:
            raise UserError(_("Connection error: %s") % str(e)[:200])
        if resp.status_code != 200:
            raise UserError(_("Failed to fetch operators (HTTP %s).") % resp.status_code)

        Map = self.env["levelix.calls.user.map"]
        created = 0
        for op in resp.json():
            email = op.get("email")
            if not email:
                continue
            existing = Map.search(
                [("backend_email", "=ilike", email), ("company_id", "=", self.company_id.id)],
                limit=1,
            )
            vals = {"backend_name": op.get("full_name"), "backend_user_id": op.get("user_id")}
            if existing:
                existing.write(vals)
            else:
                # Odoo user'ini email bo'yicha avto-taklif (login/email mos kelsa)
                user = self.env["res.users"].search(
                    [("company_ids", "in", self.company_id.id),
                     "|", ("login", "=ilike", email), ("email", "=ilike", email)],
                    limit=1,
                )
                Map.create({
                    "backend_email": email,
                    "user_id": user.id if user else False,
                    "company_id": self.company_id.id,
                    **vals,
                })
                created += 1

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Operators"),
                "message": _("%s new operator(s) added. Choose the Odoo user.") % created,
                "type": "success",
            },
        }
