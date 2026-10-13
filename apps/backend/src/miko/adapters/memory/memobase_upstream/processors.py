from .env import ContanstTable
FactResponse = dict
AddProfile = dict

def merge_by_topic_sub_topics(new_facts: list[FactResponse]):
    topic_subtopic = {}
    for nf in new_facts:
        key = (nf[ContanstTable.topic], nf[ContanstTable.sub_topic])
        if key in topic_subtopic and isinstance(nf["memo"], str):
            topic_subtopic[key]["memo"] += f"; {nf['memo']}"
            continue
        topic_subtopic[key] = nf
    return list(topic_subtopic.values())


def deduplicate_profiles(profiles: list[AddProfile]) -> list[AddProfile]:
    topic_subtopic = {}
    for nf in profiles:
        key = (
            nf["attributes"][ContanstTable.topic],
            nf["attributes"][ContanstTable.sub_topic],
        )
        if key in topic_subtopic:
            topic_subtopic[key]["content"] += f"; {nf['content']}"
            continue
        topic_subtopic[key] = nf
    return list(topic_subtopic.values())

