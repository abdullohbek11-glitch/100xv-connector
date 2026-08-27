# -*- coding: utf-8 -*-
"""Operator moslash: Levelix backend operatori (EMAIL) ↔ Odoo user.

Kalit — EMAIL (raqam emas; admin uchun tushunarli). Backend'dan operatorlar ro'yxati
tortiladi (email + ism), admin har biriga Odoo user'ini dropdown'dan tanlaydi.
Email backend va Odoo'da bir xil bo'lsa — ingest AVTO-moslaydi (mapping shart emas).
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LevelixUserMap(models.Model):
    _name = "levelix.calls.user.map"
    _description = "XCall Operator Mapping"
    _rec_name = "backend_email"
    _order = "backend_email"

    backend_email = fields.Char(string="Backend operator (email)", required=True, index=True)
    backend_name = fields.Char(string="Backend name", readonly=True)  # ro'yxatdan (ma'lumot)
    backend_user_id = fields.Integer(string="Backend ID", readonly=True)  # ma'lumot
    user_id = fields.Many2one("res.users", string="Odoo user")
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company,
    )

    _uniq_email = models.Constraint(
        "unique(backend_email, company_id)",
        "This backend operator is already mapped in this company.",
    )

    @api.constrains("user_id", "company_id")
    def _check_user_company(self):
        """Moslangan Odoo user shu mapping kompaniyasiga a'zo bo'lishi SHART —
        aks holda A kompaniya mappingiga B kompaniya useri yozib qo'yiladi (A.7.1/4.4)."""
        for rec in self:
            if rec.user_id and rec.company_id and rec.company_id not in rec.user_id.company_ids:
                raise ValidationError(
                    _("The mapped user must be a member of the mapping's company.")
                )
