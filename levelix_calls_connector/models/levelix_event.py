# -*- coding: utf-8 -*-
"""Levelix Calls — received webhook events log.

In phase 1 every incoming (signature-valid) event is written here. `event_uid`
(X-LC-Event-Id) is unique — duplicate deliveries are written once. Live events may
not carry an event_uid (NULL) — those are not deduplicated.

Phase 2: turn state='received' records into CRM / activities.
"""
from odoo import fields, models


class LevelixCallsEvent(models.Model):
    _name = "levelix.calls.event"
    _description = "XCall Received Event"
    _order = "received_at desc, id desc"
    _rec_name = "event_type"
    _check_company_auto = True

    config_id = fields.Many2one(
        "levelix.calls.config", string="Connection", ondelete="cascade", index=True,
        check_company=True,
    )
    company_id = fields.Many2one("res.company", string="Company", index=True, required=True)
    event_uid = fields.Char(string="Event ID", index=True, copy=False)  # X-LC-Event-Id
    event_type = fields.Char(string="Event Type", index=True)
    call_uid = fields.Char(string="Call UID", index=True)
    payload = fields.Text(string="Payload (JSON)")
    signature_ok = fields.Boolean(string="Signature Valid", default=False)
    state = fields.Selection(
        selection=[
            ("received", "Received"),
            ("processed", "Processed"),
            ("ignored", "Ignored"),
        ],
        default="received",
        index=True,
    )
    received_at = fields.Datetime(string="Received At", default=fields.Datetime.now)

    _uniq_event_uid = models.Constraint(
        "unique(event_uid)", "This event has already been received."
    )
