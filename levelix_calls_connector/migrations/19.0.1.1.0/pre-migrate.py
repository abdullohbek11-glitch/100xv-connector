# -*- coding: utf-8 -*-
"""19.0.1.1.0 — call/event.company_id endi `required`.
Eski NULL yozuvlarni asosiy kompaniyaga to'ldiramiz (NOT NULL constraint qo'yilishidan OLDIN).
"""


def migrate(cr, version):
    main = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"
    for table in ("levelix_calls_call", "levelix_calls_event"):
        cr.execute(
            "UPDATE %s SET company_id = %s WHERE company_id IS NULL" % (table, main)
        )
