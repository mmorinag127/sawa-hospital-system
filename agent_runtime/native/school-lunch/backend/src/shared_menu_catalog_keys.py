"""Exact catalog identities and historical editor vocabulary."""
import hashlib
import unicodedata

ANSWER_CHOICES = ('proposal', 'corrected', 'not_applicable')


def review_counts(provenance):
    questions = (provenance.get('pattern_review') or {}).get('questions') or []
    return {'question_count': len(questions),
            'unanswered_count': sum(question.get('answer') not in ANSWER_CHOICES for question in questions)}


def revision_counts(document, provenance):
    return {'ingredient_count': len(document['ingredients']), 'step_count': len(document['steps']),
            **review_counts(provenance)}


def menu_identity(name):
    return ''.join(unicodedata.normalize('NFKC', name).split()).lstrip('★')


def menu_identity_key(name):
    return hashlib.sha256(menu_identity(name).encode('utf-8')).hexdigest()


def catalog_search_text(name):
    return unicodedata.normalize('NFKC', name).lower()


def vocabulary(document, revision_id):
    values = {('operation', step['operation']) for step in document.get('steps', []) if step.get('operation')}
    values.update(('ingredient', item['name']) for item in document.get('ingredients', []) if item.get('name'))
    return [{'kind': kind, 'value': value, 'first_revision_id': revision_id} for kind, value in sorted(values)]
