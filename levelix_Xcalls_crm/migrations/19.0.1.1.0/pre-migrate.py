# -*- coding: utf-8 -*-
"""19.0.1.1.0 — user.map.company_id endi `required` va rule QAT'IY.
Eski NULL mappinglarni asosiy kompaniyaga to'ldiramiz (NOT NULL constraintдan OLDIN).
"""


def migrate(cr, version):
    cr.execute(
        "UPDATE levelix_calls_user_map "
        "SET company_id = (SELECT id FROM res_company ORDER BY id LIMIT 1) "
        "WHERE company_id IS NULL"
    )
