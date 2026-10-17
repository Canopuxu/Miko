CREATE TABLE IF NOT EXISTS person_aliases (
    subject TEXT NOT NULL REFERENCES people(id),
    name TEXT NOT NULL,
    PRIMARY KEY(subject,name)
);
INSERT OR IGNORE INTO person_aliases VALUES('miko','Miko'),('owner','橙汁');
INSERT OR IGNORE INTO person_aliases SELECT id,display_name FROM people WHERE id IN ('miko','owner');
