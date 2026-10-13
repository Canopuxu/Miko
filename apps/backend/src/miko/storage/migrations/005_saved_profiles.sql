UPDATE profile_entries SET enabled=1 WHERE kind IN ('overview','fact') AND enabled=0;
