"""Lexical source constraints independent of a model's selected quotations.

Only explicitly named registered ingredients are bound. Unresolvable generic
references stay in the source; no nearest-name or ingredient substitution.
"""
import re


def extract_constraints(menu, normalize, aliases):
    names = {m['id']: aliases(m['name']) for m in menu['materials']}
    text = normalize(menu['instructions'])
    rules = []; earlier = set()
    # Japanese has no whitespace word boundary. Accept explicit grammatical
    # delimiters; a longer noun such as ごま油 must not bind to ごま alone.
    boundary = r'$|[、,，・。()（）「」\[\]＋+はをがにとでのもやへ]|から|まで|及び|および'

    def named_spans(clause):
        spans = []
        for mid, variants in names.items():
            for name in variants:
                if name:
                    spans.extend((m.start(), m.end(), mid) for m in re.finditer(re.escape(name), clause))
        # A mention of ごま油 is not also an arrival of the registered ごま.
        # Compare spans, not a globally shortened alias or the first candidate.
        maximal = [span for span in spans if not any(
            other[0] <= span[0] and span[1] <= other[1]
            and (other[0], other[1]) != (span[0], span[1]) for other in spans)]
        resolved = [s for s in maximal if re.match(boundary, clause[s[1]:])]
        unresolved = []
        for start, end, mid in maximal:
            if (start,end,mid) in resolved:
                continue
            tail=re.split(boundary,clause[end:],maxsplit=1)[0]
            unresolved.append(dict(material_id=mid,surface=clause[start:end]+tail))
        return resolved, unresolved

    def mentions(clause):
        resolved,_ = named_spans(clause)
        found = [(min(s[0] for s in resolved if s[2] == mid), mid)
                 for mid in names if any(s[2] == mid for s in resolved)]
        return [mid for _, mid in sorted(found)]

    for clause in filter(None, re.split('[。\n]', text)):
        _,unresolved=named_spans(clause)
        for entry in unresolved:
            rule=dict(kind='unresolved_material',**entry,quote=clause)
            if rule not in rules:
                rules.append(rule)
        present = mentions(clause)
        if re.search('最後に|仕上げに|後から', clause):
            first, last = re.split('最後に|仕上げに|後から', clause, maxsplit=1)
            late = mentions(last)
            for before in sorted((earlier | set(mentions(first))) - set(late)):
                for after in late:
                    rules.append(dict(kind='order', before=before, after=after, quote=clause))
        if 'の順' in clause:
            sequence = mentions(clause.split('の順')[0])
            for before, after in zip(sequence, sequence[1:]):
                rules.append(dict(kind='order', before=before, after=after, quote=clause))
        methods = {'roast': r'炒る|炒って|炒り', 'boil': r'(?:下|塩)?(?:茹で|ゆで)',
                   'steam': r'蒸す|蒸し', 'stir_fry': r'炒め', 'stock': r'だしを取|出汁を取'}
        for method, pattern in methods.items():
            for match in re.finditer(pattern, clause):
                # A direct object/subject, including a coordinated list, must
                # immediately precede the verb. Ignore unrelated earlier food.
                prefix = clause[:match.start()]
                ingredient = '(?:' + '|'.join(re.escape(a) for group in names.values() for a in group) + ')'
                subject = re.search('(' + ingredient + '(?:[・、,，]' + ingredient + ')*)'
                                    r'(?:は|を)(?:それぞれ|別々に|先に|軽く|よく|塩|下)*$', prefix)
                if subject:
                    for mid in mentions(subject.group(1)):
                        rules.append(dict(kind='action', material_id=mid, method=method, quote=clause))
        earlier.update(present)
    return rules


def validate_constraints(rules, nodes, blocked):
    for rule in rules:
        if rule['kind']=='unresolved_material':
            if rule['material_id'] not in blocked:
                raise ValueError('原文に未解決の食材表現: '+rule['surface']+'。登録材料の部分名へ置換しない')
        elif rule['kind']=='action':
            mid = rule['material_id']
            if mid in blocked:
                continue
            satisfied = any(mid in p.get('material_ids', []) and p.get('method') == rule['method']
                            for n in nodes for p in n['phases'] if isinstance(p, dict))
            if rule['method']=='boil':
                satisfied |= any(mid in n['material_ids'] and n['operation']=='下茹で' for n in nodes)
            if not satisfied:
                raise ValueError(f'原文明示加工が材料に適用されていない {mid}/{rule["method"]}')
        else:
            a, b = rule['before'], rule['after']
            if a in blocked or b in blocked:
                continue
            for n in nodes:
                phases = [p for p in n['phases'] if isinstance(p, dict)]
                arrival = {mid: next((i for i,p in enumerate(phases) if mid in p.get('material_ids',[])), None)
                           for mid in [a,b]}
                if arrival[a] is not None and arrival[b] is not None and arrival[a] >= arrival[b]:
                    # If both arrived already mixed through the same upstream
                    # stream, this node is not the location of the late addition.
                    shared = [p for p in phases if a in p.get('arriving_material_ids',[]) and b in p.get('arriving_material_ids',[])]
                    origins = n.get('arrival_origins', {})
                    if shared and origins.get(a) == origins.get(b) and origins.get(a):
                        continue
                    raise ValueError(f'原文の後入れ/投入順と矛盾 {a}→{b}')
