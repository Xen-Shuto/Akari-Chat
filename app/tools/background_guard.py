"""Same-room unreviewed outcomes gate background work, never conversations."""
BLOCKERS=(('semantic_consolidation_runs',"u.state='unknown'"),('autonomy_runs',"u.state='unknown' AND COALESCE(json_extract(u.payload,'$.reviewed'),0)=0"),
 ('sleep_night_jobs',"u.state='unknown' AND u.reviewed=0"),
 ('url_watch_checks',"u.state='unknown' AND COALESCE(json_extract(u.payload,'$.reviewed'),0)=0"),
 ('night_autosaves',"u.state='unknown'"),('research_maintenance_runs',"u.state='unknown'"),
 ('entity_maintenance_runs',"u.state='unknown'"),('memory_save_jobs',"u.state='unknown'"),
 ('manual_memory_runs',"u.state='unknown'"))
def ready_sql(room, *, ignore_manual=False):
 # All callers supply a source-code constant alias, never a user-provided value.
 if room not in ('pending.room','n.room','a.room','j.room','selected.room'):raise ValueError('Invalid internal room alias')
 return 'NOT EXISTS(SELECT 1 FROM room_states archived_room WHERE archived_room.room='+room+' AND archived_room.archived=1) AND '+' AND '.join('NOT EXISTS(SELECT 1 FROM '+table+' u WHERE u.room='+room+' AND '+condition+')' for table,condition in BLOCKERS if not (ignore_manual and table=='manual_memory_runs'))
def uncertain(db,room, *, ignore_manual=False):
 return not bool(db.execute('WITH selected(room) AS (VALUES (?)) SELECT '+ready_sql('selected.room',ignore_manual=ignore_manual)+' FROM selected',(room,)).fetchone()[0])
