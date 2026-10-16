"""Editable presentation categories over the existing profile entry API."""
import json
import uuid
from pathlib import Path

from miko.storage.search import terms


def defaults(subject):
    template = json.loads(Path(__file__).with_name('profile-categories.json').read_text('utf-8'))
    groups = [(group['name'], group['children']) for group in template[subject]]
    return [{'id': subject + '-core' if index == 0 else uuid.uuid4().hex, 'name': name,
             'children': [{'id': uuid.uuid4().hex, 'name': child} for child in children]}
            for index, (name, children) in enumerate(groups)]


def paths(categories):
    result = {}
    for root in categories:
        result[root['id']] = root['name']
        for child in root['children']:
            result[child['id']] = root['name'] + '/' + child['name']
    return result


class ProfileCategories:
    def __init__(self, chat):
        self.chat = chat
        with chat.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS profile_library(subject TEXT PRIMARY KEY, categories TEXT NOT NULL)')

    def get(self, subject):
        with self.chat.connect() as db:
            row = db.execute('SELECT categories FROM profile_library WHERE subject=?', (subject,)).fetchone()
            if row:
                return json.loads(row[0])
            categories = defaults(subject)
            known = set(paths(categories).values())
            # Keep the original categorization readable, without rewriting content.
            for row in db.execute("SELECT DISTINCT category FROM profile_entries WHERE subject=? AND kind IN ('fact','overview')", (subject,)):
                name = row[0]
                if name and name not in known:
                    parts = name.split('/', 1)
                    root = next((c for c in categories if c['name'] == parts[0]), None)
                    if root is None:
                        root = {'id': uuid.uuid4().hex, 'name': parts[0], 'children': []}
                        categories.append(root)
                    if len(parts) == 2:
                        root['children'].append({'id': uuid.uuid4().hex, 'name': parts[1]})
                    known.add(name)
            db.execute('INSERT INTO profile_library VALUES(?,?)', (subject, json.dumps(categories, ensure_ascii=False)))
            return categories

    def save(self, subject, categories):
        before = paths(self.get(subject))
        after = paths(categories)
        with self.chat.connect() as db:
            # Rename or remove a category without changing any setting's content,
            # author, kind, scope, or activation state. The FTS follows the label.
            for row in db.execute("SELECT * FROM profile_entries WHERE subject=? AND kind IN ('fact','overview')", (subject,)).fetchall():
                old_id = next((key for key, path in before.items() if path == row['category']), None)
                if old_id is None:
                    continue
                category = after.get(old_id, '')
                if category == row['category']:
                    continue
                db.execute('UPDATE profile_entries SET category=? WHERE id=?', (category, row['id']))
                indexed = ' '.join(sorted(terms(' '.join((row['title'], category, row['keywords'], row['content'])), limit=None)))
                db.execute('DELETE FROM profile_fts WHERE entry_id=?', (row['id'],))
                db.execute('INSERT INTO profile_fts VALUES(?,?)', (row['id'], indexed))
            db.execute('UPDATE profile_library SET categories=? WHERE subject=?',
                       (json.dumps(categories, ensure_ascii=False), subject))
        return categories
