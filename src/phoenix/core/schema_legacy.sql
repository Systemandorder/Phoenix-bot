CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                target TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

CREATE TABLE auto_calendars (
                chat_id INTEGER,
                message_id INTEGER,
                group_name TEXT
            );

CREATE TABLE auto_dashboards (
            chat_id INTEGER,
            message_id INTEGER,
            sch_id TEXT,
            PRIMARY KEY (chat_id, message_id)
        );

CREATE TABLE bkb_cid (cid TEXT PRIMARY KEY, rel TEXT);

CREATE TABLE bkb_fav (user_id INTEGER, rel TEXT, PRIMARY KEY(user_id, rel));

CREATE TABLE bkb_favx (user_id INTEGER, kind TEXT, ref TEXT, PRIMARY KEY(user_id, kind, ref));

CREATE TABLE bot_feedback (
                user_id INTEGER PRIMARY KEY,
                ui_score INTEGER,
                speed_score INTEGER,
                features_score INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

CREATE TABLE bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

CREATE TABLE calendar_deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                group_name TEXT,
                deadline_date TEXT, -- Формат: DD.MM.YYYY
                description TEXT
            , subject TEXT DEFAULT 'Загальне');

CREATE TABLE cs2_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, first_msg INTEGER, last_msg INTEGER,
            thread_id INTEGER, ts REAL, text TEXT, emb BLOB, kind TEXT, has_link INTEGER,
            fname TEXT, msgs TEXT);

CREATE VIRTUAL TABLE cs2_fts USING fts5(text, chat_id UNINDEXED);

CREATE TABLE cs2_hist (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, q TEXT, ts REAL);

CREATE TABLE cs2_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            chat_id INTEGER, query TEXT, rank INTEGER, p REAL, e REAL);

CREATE TABLE cs2_saved (
            chat_id INTEGER PRIMARY KEY, title TEXT, alias TEXT, ts REAL, used REAL);

CREATE TABLE cs2_state (
            chat_id INTEGER PRIMARY KEY, title TEXT, last_msg INTEGER DEFAULT 0);

CREATE TABLE custom_ttv_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, name TEXT, prompt TEXT, extra TEXT);

CREATE TABLE dynamic_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sch_id INTEGER,
                week_type INTEGER,
                day_code TEXT,
                l_time TEXT,
                l_subject TEXT,
                l_teacher TEXT,
                l_link TEXT,
                l_location TEXT, owner_id INTEGER DEFAULT NULL, l_conf_id TEXT DEFAULT '-', l_conf_pass TEXT DEFAULT '-',
                FOREIGN KEY(sch_id) REFERENCES schedule_list(id)
            );

CREATE TABLE file_permissions (
                file_uid TEXT PRIMARY KEY,
                file_path TEXT,
                group_name TEXT,
                can_view INTEGER DEFAULT 1
            );

CREATE TABLE group_chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                chat_title TEXT,
                user_id INTEGER,
                username TEXT,
                display_name TEXT,
                message_text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

CREATE TABLE knowledge_archive (
                item_code TEXT PRIMARY KEY,
                entry_type TEXT,
                category TEXT,
                title TEXT,
                threat_level TEXT,
                affected_targets TEXT,
                commands TEXT,
                description TEXT,
                code_example TEXT
            );

CREATE TABLE kyiv_ballistic_notified (
                target_key TEXT PRIMARY KEY,
                notified_at TEXT
            );

CREATE TABLE kyiv_channel_reports (
                channel TEXT PRIMARY KEY, threat_type TEXT, location TEXT,
                count INTEGER, summary TEXT, updated_at DATETIME
            , specific_model TEXT);

CREATE TABLE kyiv_custom_places (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, added_by INTEGER, added_at TEXT, important INTEGER DEFAULT 0);

CREATE TABLE kyiv_monitor_seen (
                channel TEXT, msg_id INTEGER, PRIMARY KEY (channel, msg_id)
            );

CREATE TABLE kyiv_target_reports (
                target_id INTEGER, channel TEXT, msg_time TEXT, snippet TEXT, is_repost INTEGER DEFAULT 0,
                PRIMARY KEY (target_id, channel)
            );

CREATE TABLE kyiv_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                threat_type TEXT, model TEXT, count INTEGER,
                route TEXT DEFAULT '', first_seen TEXT, last_seen TEXT,
                status TEXT DEFAULT 'active'
            );

CREATE TABLE kyiv_threat_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                location TEXT, threat_type TEXT, count INTEGER,
                summary TEXT, updated_at DATETIME
            );

CREATE TABLE kyiv_watch_locations (
                    user_id INTEGER, location TEXT, PRIMARY KEY (user_id, location)
                );

CREATE TABLE kyiv_watch_notified (
                    target_id INTEGER, user_id INTEGER, PRIMARY KEY (target_id, user_id)
                );

CREATE TABLE monitor_channels (
                channel TEXT PRIMARY KEY, added_by INTEGER, added_at DATETIME
            , title TEXT);

CREATE TABLE org_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    group_name TEXT,
                    category TEXT,
                    content TEXT
                , parent_id INTEGER DEFAULT 0, is_folder INTEGER DEFAULT 0);

CREATE TABLE phoenix_limits (
                    user_id INTEGER, day TEXT, count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                );

CREATE TABLE phoenix_limits_heavy (
                    user_id INTEGER, day TEXT, count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                );

CREATE VIRTUAL TABLE phoenix_log USING fts5(
                    chat_id UNINDEXED, thread_id UNINDEXED, user_id UNINDEXED,
                    author UNINDEXED, role UNINDEXED, ts UNINDEXED, text
                );

CREATE TABLE polls (
                poll_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                group_name TEXT,
                question TEXT,
                options TEXT,
                is_active INTEGER DEFAULT 1,
                is_hidden INTEGER DEFAULT 0
            );

CREATE TABLE schedule_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                group_name TEXT,
                folder_id TEXT
            );

CREATE TABLE server_paths (
                uid TEXT PRIMARY KEY,
                full_path TEXT
            , click_count INTEGER DEFAULT 0);

CREATE TABLE sms_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        sender TEXT DEFAULT '', include_words TEXT DEFAULT '[]', exclude_words TEXT DEFAULT '[]');

CREATE TABLE sms_rules (
                user_id INTEGER PRIMARY KEY, token TEXT UNIQUE,
                enabled INTEGER DEFAULT 1, senders TEXT DEFAULT '[]',
                include_words TEXT DEFAULT '[]', exclude_words TEXT DEFAULT '[]'
            );

CREATE TABLE tag_content (
                tag_path TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL
            );

CREATE TABLE ui_backup_v4 (key TEXT, value TEXT, ts INTEGER);

CREATE TABLE user_clicks (
                user_id INTEGER,
                file_uid TEXT,
                clicks INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, file_uid)
            );

CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                group_name TEXT,
                is_vip INTEGER DEFAULT 0,
                has_paid INTEGER DEFAULT 0,
                free_clicks INTEGER DEFAULT 5
            , subscription_end TIMESTAMP, ui_theme TEXT DEFAULT 'DEFAULT', username TEXT, energy_group TEXT);

CREATE TABLE votes (
                poll_id INTEGER,
                user_id INTEGER,
                username TEXT,
                option_index INTEGER,
                PRIMARY KEY (poll_id, user_id)
            );

CREATE INDEX cs2_chunks_chat ON cs2_chunks(chat_id);
