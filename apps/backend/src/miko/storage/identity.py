import re


DEFAULT_NAMES = {'miko': 'Miko', 'owner': '橙汁'}
TEXT_FIELDS = frozenset(('title', 'category', 'topic', 'sub_topic', 'memo', 'content', 'text',
    'keywords', 'automatic', 'manual', 'manual_corrections', 'owner_relationship',
    'miko_relationship', 'description', 'note', 'web', 'qq', 'qq_base', 'life', 'expressions',
    'event', 'result', 'done_when', 'current_state'))
ORIGINAL_FIELDS = frozenset(('sources', 'source_messages', 'history', 'episodes', 'context'))


def rename_person(db, subject, name):
    previous = db.execute('SELECT display_name FROM people WHERE id=?', (subject,)).fetchone()[0]
    db.executemany('INSERT OR IGNORE INTO person_aliases(subject,name) VALUES(?,?)',
                   [(subject, previous), (subject, name)])
    db.execute('UPDATE people SET display_name=? WHERE id=?', (name, subject))


class IdentityNames:
    def __init__(self, store):
        with store.connect() as db:
            self.current = {r['id']: r['display_name'] for r in db.execute(
                "SELECT id,display_name FROM people WHERE id IN ('miko','owner')")}
            rows = db.execute('SELECT subject,name FROM person_aliases').fetchall()
        candidates = {}
        for row in rows:
            candidates.setdefault(row['name'], set()).add(row['subject'])
        self.aliases = {}
        for name, subjects in candidates.items():
            current = [subject for subject, value in self.current.items() if value == name]
            owners = current or list(subjects)
            if len(owners) == 1:
                self.aliases[name] = owners[0]

    def replace(self, text, canonical=False):
        aliases = self.aliases
        if not text or not aliases:
            return text
        # Person IDs and JSON keys are never passed through the material walker.
        # Keep the reserved lowercase IDs intact in existing prompt templates.
        usable = [name for name in aliases if name not in DEFAULT_NAMES]
        if not usable:
            return text
        patterns = []
        for name in sorted(usable, key=len, reverse=True):
            start = r'(?<![A-Za-z0-9_])' if name[0].isascii() and name[0].isalnum() else ''
            end = r'(?![A-Za-z0-9_])' if name[-1].isascii() and name[-1].isalnum() else ''
            patterns.append(start + re.escape(name) + end)
        lookup = {name.casefold(): subject for name, subject in aliases.items()}
        target = DEFAULT_NAMES if canonical else self.current
        def substitute(match):
            if not canonical and match[0] in DEFAULT_NAMES:
                return match[0]
            return target[lookup[match[0].casefold()]]
        return re.sub('|'.join(patterns), substitute, text, flags=re.IGNORECASE)

    def display(self, text):
        return self.replace(text)

    def canonical(self, text):
        return self.replace(text, canonical=True)

    def mentioned(self, text, people):
        candidates = {}
        for subject, name in people.items():
            if name:
                candidates.setdefault(name.casefold(), set()).add(subject)
        for name, subject in self.aliases.items():
            candidates.setdefault(name.casefold(), set()).add(subject)
        result = []
        for name, subjects in sorted(candidates.items(), key=lambda item: len(item[0]), reverse=True):
            if len(subjects) != 1:
                continue
            start = r'(?<![A-Za-z0-9_])' if name[0].isascii() and name[0].isalnum() else ''
            end = r'(?![A-Za-z0-9_])' if name[-1].isascii() and name[-1].isalnum() else ''
            if re.search(start + re.escape(name) + end, text, re.IGNORECASE):
                subject = next(iter(subjects))
                if subject not in result:
                    result.append(subject)
        return result

    def material(self, value, *, canonical=False, fields=TEXT_FIELDS):
        def walk(item, field=''):
            if field in ORIGINAL_FIELDS:
                return item
            if isinstance(item, str):
                return self.replace(item, canonical) if field in fields else item
            if isinstance(item, list):
                return [walk(child, field) for child in item]
            if isinstance(item, dict):
                return {key: walk(child, key) for key, child in item.items()}
            return item
        return walk(value)
