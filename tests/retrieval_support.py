import tempfile
from pathlib import Path

from miko.adapters.memory.retrieval import LocalRecall
from miko.domain.conversation.context_selection import candidates, scoring_document, source_key, select as choose


async def select(provider, query, context, **options):
    for group, rows in context.items():
        if not isinstance(rows,list) or group.startswith('_'):
            continue
        for n,row in enumerate(rows):
            if isinstance(row,dict):row.setdefault('id',f'{group}:{n}')
    _,rows=candidates(context)
    with tempfile.TemporaryDirectory() as folder:
        local=LocalRecall(Path(folder)/'recall.sqlite3')
        local.sync({source_key(row):{'description':scoring_document(row),'peer':row['source'].get('peer',''),'row':row}
                    for row in rows})
        return await choose(provider,query,context,local=local,**options)
