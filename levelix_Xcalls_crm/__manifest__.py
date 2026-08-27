# -*- coding: utf-8 -*-
{
    "name": "XCall CRM",
    "version": "19.0.1.1.0",
    "summary": "Log XCall calls on CRM leads with call info and audio recording",
    "description": """
XCall CRM
===============
Extends **XCall Connector**: every incoming call event is matched to a
CRM lead by phone number, and the call is logged where your sales team works.

- **Smart matching** - the caller number is normalized (E.164) and matched
  against existing leads/opportunities. If nothing matches, a new opportunity
  is created automatically, so no call is ever lost.
- **Chatter log** - a single message is posted on the lead with the main call
  info (direction, duration, status, operator) plus an **audio player** and a
  collapsible details block.
- **Late audio** - when the recording finishes transcoding on the server, it is
  attached to the same chatter message automatically.
- **Operator mapping** - map a XCall backend user to an Odoo user.
- **Number-fix tool** - a one-time action to reformat badly stored
  phone/mobile numbers so matching stays reliable.

Requirements
------------
Depends on **XCall Connector** and the standard CRM app. A XCall
account and server are required.
""",
    "author": "Levelix",
    "maintainer": "Levelix",
    "support": "100levelix@gmail.com",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["levelix_calls_connector", "crm", "mail", "phone_validation"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/levelix_security.xml",
        "security/ir.model.access.csv",
        "wizard/levelix_number_fix_views.xml",
        "views/levelix_crm_views.xml",
    ],
    "images": [
        "static/description/banner.png",
        "static/description/screenshot-lead.png",
    ],
    "installable": True,
}
