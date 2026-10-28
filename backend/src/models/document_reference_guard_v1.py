"""Coordinate non-FK reference writes with retention, including Core SQL writers."""
from sqlalchemy import event, inspect

from src.db import Base

REFERENCES = {
    'orders': ('current_document_id', 'document_uri', 'message_id'),
    'order_versions': ('document_id', 'storage_uri', 'message_id'),
    'uploaded_pdfs': ('current_document_id', 'storage_uri', 'message_id'),
    'ocr_jobs': ('order_document_id', 'input_reference', None),
    'ocr_training_samples': (None, 'document_uri', None),
    'order_ocr_cache': (None, None, None),
    'order_ocr_revisions': (None, None, None),
}
PARENTS = {'ocr_training_samples', 'order_ocr_cache', 'order_ocr_revisions'}


def install(connection):
    dialect = connection.dialect.name
    if dialect not in {'sqlite', 'postgresql'}:
        raise ValueError('document reference guards require SQLite or PostgreSQL')
    present = set(inspect(connection).get_table_names())
    if not {'orders', 'order_documents'} <= present:
        return
    for table, (identity, uri, message) in REFERENCES.items():
        if table not in present:
            continue
        columns = [value for value in (identity, uri, message) if value]
        if table in PARENTS:
            columns.append('order_id')
        if table == 'orders':
            columns.append('superseded_document_ids')
        predicates = [f'id = NEW.{identity}' for _ in [0] if identity]
        if uri:
            predicates.append(f'storage_uri = NEW.{uri}')
        if message:
            predicates.append(f'source_email_id = NEW.{message}')
        name = 'sawa_reference_' + table
        if dialect == 'postgresql':
            parent = ("PERFORM 1 FROM orders WHERE id = NEW.order_id FOR KEY SHARE; "
                      "IF NOT FOUND THEN RAISE EXCEPTION 'retained_order_reference_missing'; END IF;"
                      if table in PARENTS else '')
            validate_identity = ''
            if identity:
                validate_identity = f"""IF NEW.{identity} IS NOT NULL AND NEW.{identity} <> '' THEN
                  IF TG_OP = 'INSERT' OR NEW.{identity} IS DISTINCT FROM OLD.{identity} THEN
                    IF NOT EXISTS (SELECT 1 FROM order_documents WHERE id = NEW.{identity}) THEN
                      RAISE EXCEPTION 'retained_document_reference_missing';
                    END IF;
                  END IF;
                END IF;"""
            superseded = ''
            if table == 'orders':
                members = "SELECT value FROM json_array_elements_text(CASE WHEN json_typeof(NEW.superseded_document_ids)='array' THEN NEW.superseded_document_ids ELSE '[]'::json END)"
                predicates.append('id IN (' + members + ')')
                superseded = f"""IF TG_OP = 'UPDATE' THEN previous_ids := OLD.superseded_document_ids; END IF;
                  IF EXISTS (SELECT 1 FROM ({members}) added
                    WHERE added.value IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM order_documents d WHERE d.id=added.value)
                      AND NOT EXISTS (SELECT 1 FROM json_array_elements_text(CASE WHEN json_typeof(previous_ids)='array'
                        THEN previous_ids ELSE '[]'::json END) previous WHERE previous.value=added.value)) THEN
                    RAISE EXCEPTION 'retained_document_reference_missing';
                  END IF;"""
            lock = ('PERFORM 1 FROM order_documents WHERE ' + ' OR '.join(predicates) + ' ORDER BY id FOR KEY SHARE;') if predicates else ''
            connection.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
              DECLARE previous_ids json;
              BEGIN {parent} {lock} {validate_identity} {superseded} RETURN NEW; END $$""")
            connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name} ON {table}')
            connection.exec_driver_sql(f'CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OF {", ".join(columns)} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()')
        else:
            for action in ('INSERT', 'UPDATE'):
                statements = []
                if table in PARENTS:
                    statements.append("SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM orders WHERE id=NEW.order_id) "
                                      "THEN RAISE(ABORT, 'retained_order_reference_missing') END;")
                if identity:
                    changed = 'TRUE' if action == 'INSERT' else f'NEW.{identity} IS NOT OLD.{identity}'
                    statements.append(f"SELECT CASE WHEN NEW.{identity} IS NOT NULL AND NEW.{identity} <> '' AND ({changed}) "
                        f"AND NOT EXISTS (SELECT 1 FROM order_documents WHERE id=NEW.{identity}) "
                        "THEN RAISE(ABORT, 'retained_document_reference_missing') END;")
                if table == 'orders':
                    old = "'[]'" if action == 'INSERT' else "CASE WHEN json_type(OLD.superseded_document_ids)='array' THEN OLD.superseded_document_ids ELSE '[]' END"
                    statements.append("SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(CASE "
                        "WHEN json_type(NEW.superseded_document_ids)='array' THEN NEW.superseded_document_ids ELSE '[]' END) added "
                        "WHERE added.value IS NOT NULL AND NOT EXISTS (SELECT 1 FROM order_documents d WHERE d.id=added.value) "
                        f"AND NOT EXISTS (SELECT 1 FROM json_each({old}) previous WHERE previous.value=added.value)) "
                        "THEN RAISE(ABORT, 'retained_document_reference_missing') END;")
                if statements:
                    event_kind = action if action == 'INSERT' else 'UPDATE OF ' + ', '.join(columns)
                    connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}_{action.lower()}')
                    connection.exec_driver_sql(f'CREATE TRIGGER {name}_{action.lower()} BEFORE {event_kind} ON {table} FOR EACH ROW BEGIN {" ".join(statements)} END')


def uninstall(connection):
    present = set(inspect(connection).get_table_names())
    for table in REFERENCES:
        if table not in present:
            continue
        name = 'sawa_reference_' + table
        if connection.dialect.name == 'postgresql':
            connection.exec_driver_sql(f'DROP TRIGGER {name} ON {table}')
            connection.exec_driver_sql(f'DROP FUNCTION {name}()')
        else:
            for action in ('insert', 'update'):
                connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}_{action}')


@event.listens_for(Base.metadata, 'after_create')
def _install_new_database(_metadata, connection, tables, **_kwargs):
    if {'orders', 'order_documents'} <= {table.name for table in tables}:
        install(connection)
