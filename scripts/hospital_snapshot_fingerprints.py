"""Fingerprint original column values without sorting full rows in Cloud SQL."""

import hashlib


def fingerprints(connection, original_columns):
    quote = connection.dialect.identifier_preparer.quote_identifier
    result = {}
    for setting in ("TIME ZONE 'UTC'", "DateStyle = 'ISO, YMD'",
                    "IntervalStyle = 'postgres'", "extra_float_digits = 3", "bytea_output = 'hex'"):
        connection.exec_driver_sql("SET LOCAL " + setting)
    for (schema, table), names in original_columns.items():
        projection = ",".join(map(quote, names))
        relation = quote(schema) + "." + quote(table)
        query = ("SELECT sha256(convert_to(row_to_json(t)::text, 'UTF8')) "
                 f"FROM (SELECT {projection} FROM {relation}) t")
        with connection.exec_driver_sql(query, execution_options={"stream_results": True}) as rows:
            digests = sorted(bytes(value) for value, in rows)
        digest = hashlib.sha256()
        for value in digests:
            digest.update(value)
        result[schema + "." + table] = {"rows": len(digests), "sha256": digest.hexdigest()}
    return result
