-- Adminer 5.4.1 PostgreSQL 16.11 dump

DROP TABLE IF EXISTS "container_movement_log";
DROP SEQUENCE IF EXISTS container_movement_log_id_seq;
CREATE SEQUENCE container_movement_log_id_seq INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;

CREATE TABLE "public"."container_movement_log" (
    "id" bigint DEFAULT nextval('container_movement_log_id_seq') NOT NULL,
    "container_id" character varying(50) NOT NULL,
    "action_time" timestamptz DEFAULT now() NOT NULL,
    "action_type" character varying(50),
    "from_location" character varying(100),
    "to_location" character varying(100),
    "operator_id" character varying(100),
    CONSTRAINT "container_movement_log_pkey" PRIMARY KEY ("id")
)
WITH (oids = false);

CREATE INDEX idx_cml_container_time ON public.container_movement_log USING btree (container_id, action_time DESC);


DROP TABLE IF EXISTS "container_status";
DROP SEQUENCE IF EXISTS container_status_id_seq;
CREATE SEQUENCE container_status_id_seq INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;

CREATE TABLE "public"."container_status" (
    "id" bigint DEFAULT nextval('container_status_id_seq') NOT NULL,
    "equipment_id" character varying(50) NOT NULL,
    "port_id" character varying(20) NOT NULL,
    "container_id" character varying(50),
    "lot_id" character varying(50),
    "status" character varying(20) DEFAULT 'normal' NOT NULL,
    "error_code" character varying(20),
    "last_updated" timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT "container_status_pkey" PRIMARY KEY ("id")
)
WITH (oids = false);

CREATE INDEX idx_cs_equipment ON public.container_status USING btree (equipment_id);

CREATE INDEX idx_cs_equipment_port ON public.container_status USING btree (equipment_id, port_id);

CREATE INDEX idx_cs_status ON public.container_status USING btree (status) WHERE ((status)::text <> 'normal'::text);


DROP TABLE IF EXISTS "equipment_connection";
DROP SEQUENCE IF EXISTS equipment_connection_id_seq;
CREATE SEQUENCE equipment_connection_id_seq INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;

CREATE TABLE "public"."equipment_connection" (
    "id" bigint DEFAULT nextval('equipment_connection_id_seq') NOT NULL,
    "equipment_id" character varying(50) NOT NULL,
    "last_heartbeat" timestamptz,
    "connection_status" character varying(20) DEFAULT 'connected' NOT NULL,
    "host_ip" character varying(45),
    CONSTRAINT "equipment_connection_pkey" PRIMARY KEY ("id")
)
WITH (oids = false);

CREATE UNIQUE INDEX equipment_connection_equipment_id_key ON public.equipment_connection USING btree (equipment_id);

INSERT INTO "equipment_connection" ("id", "equipment_id", "last_heartbeat", "connection_status", "host_ip") VALUES
(1,	'EQ-4721',	'2026-03-18 15:07:58.922516+00',	'connected',	'192.168.1.101'),
(2,	'EQ-4722',	'2026-03-18 13:12:58.922516+00',	'timeout',	'192.168.1.102');

DROP TABLE IF EXISTS "equipment_event_log";
DROP SEQUENCE IF EXISTS equipment_event_log_id_seq;
CREATE SEQUENCE equipment_event_log_id_seq INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;

CREATE TABLE "public"."equipment_event_log" (
    "id" bigint DEFAULT nextval('equipment_event_log_id_seq') NOT NULL,
    "equipment_id" character varying(50) NOT NULL,
    "event_time" timestamptz NOT NULL,
    "event_code" character varying(20),
    "event_desc" text,
    CONSTRAINT "equipment_event_log_pkey" PRIMARY KEY ("id")
)
WITH (oids = false);

CREATE INDEX idx_eel_equipment_time ON public.equipment_event_log USING btree (equipment_id, event_time DESC);


DROP TABLE IF EXISTS "error_code_reference";
CREATE TABLE "public"."error_code_reference" (
    "error_code" character varying(20) NOT NULL,
    "error_desc" text NOT NULL,
    "recommended_action" text,
    CONSTRAINT "error_code_reference_pkey" PRIMARY KEY ("error_code")
)
WITH (oids = false);

INSERT INTO "error_code_reference" ("error_code", "error_desc", "recommended_action") VALUES
('E001',	'Container 未正確放置於 Port',	'重新放置 Container，確認卡扣到位'),
('E002',	'Port 感測器異常',	'通知設備工程師檢查感測器'),
('E003',	'Container ID 讀取失敗',	'清潔條碼後重試，若仍失敗則更換 Container');

DROP TABLE IF EXISTS "system_config_log";
DROP SEQUENCE IF EXISTS system_config_log_id_seq;
CREATE SEQUENCE system_config_log_id_seq INCREMENT 1 MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;

CREATE TABLE "public"."system_config_log" (
    "id" bigint DEFAULT nextval('system_config_log_id_seq') NOT NULL,
    "equipment_id" character varying(50) NOT NULL,
    "field_name" character varying(100) NOT NULL,
    "old_value" text,
    "new_value" text,
    "changed_at" timestamptz DEFAULT now() NOT NULL,
    "changed_by" character varying(100),
    CONSTRAINT "system_config_log_pkey" PRIMARY KEY ("id")
)
WITH (oids = false);

CREATE INDEX idx_scl_equipment_time ON public.system_config_log USING btree (equipment_id, changed_at DESC);


-- 2026-03-20 00:32:27 UTCw