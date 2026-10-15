--
-- PostgreSQL database dump
--

\restrict MMZCdDT3w5LssEAUDiH2OJn4zemJl2q3C7YbxraMOPSD4XVprsabCpPhdAyXPy9

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_api_keys (
    id uuid NOT NULL,
    agent_id uuid NOT NULL,
    name character varying(128) NOT NULL,
    key_hash character varying(64) NOT NULL,
    key_prefix character varying(24) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone
);


--
-- Name: agent_containers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_containers (
    id uuid NOT NULL,
    agent_id uuid NOT NULL,
    agent_version_id uuid NOT NULL,
    container_id character varying(80),
    status character varying(16) DEFAULT 'STARTING'::character varying NOT NULL,
    inflight_runs integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    last_health_at timestamp with time zone,
    restart_count integer DEFAULT 0 NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agent_containers_inflight CHECK ((inflight_runs >= 0)),
    CONSTRAINT ck_agent_containers_restarts CHECK ((restart_count >= 0)),
    CONSTRAINT ck_agent_containers_status CHECK (((status)::text = ANY ((ARRAY['STARTING'::character varying, 'READY'::character varying, 'UNHEALTHY'::character varying, 'STOPPED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: agent_egress_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_egress_rules (
    rule_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    host character varying(253) NOT NULL,
    methods text[] DEFAULT ARRAY['GET'::text] NOT NULL,
    path_prefix text,
    auth_secret_name character varying(64),
    auth_header character varying(64),
    description character varying(200),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agent_egress_rules_auth_pair CHECK ((((auth_secret_name IS NULL) AND (auth_header IS NULL)) OR ((auth_secret_name IS NOT NULL) AND (auth_header IS NOT NULL)))),
    CONSTRAINT ck_agent_egress_rules_methods CHECK (((array_length(methods, 1) >= 1) AND (array_length(methods, 1) <= 2)))
);


--
-- Name: agent_email_sender; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_email_sender (
    agent_id uuid NOT NULL,
    smtp_host character varying(253) NOT NULL,
    smtp_port integer NOT NULL,
    username text NOT NULL,
    from_address text NOT NULL,
    password_secret_name character varying(64) NOT NULL,
    allowed_recipients text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agent_email_sender_port CHECK ((smtp_port = ANY (ARRAY[25, 465, 587]))),
    CONSTRAINT ck_agent_email_sender_recipients CHECK (((array_length(allowed_recipients, 1) >= 1) AND (array_length(allowed_recipients, 1) <= 10)))
);


--
-- Name: agent_llm_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_llm_credentials (
    agent_id uuid NOT NULL,
    ciphertext text NOT NULL,
    last_four character varying(8) NOT NULL,
    base_url text,
    default_model character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_secrets (
    agent_id uuid NOT NULL,
    name character varying(64) NOT NULL,
    ciphertext text NOT NULL,
    last_four character varying(8) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_settings (
    agent_id uuid NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_versions (
    id uuid NOT NULL,
    agent_id uuid NOT NULL,
    semver character varying(64) NOT NULL,
    source_type character varying(16) NOT NULL,
    image_ref text NOT NULL,
    image_digest character varying(71) NOT NULL,
    version_id character varying(12) NOT NULL,
    image_platform character varying(64),
    image_size_bytes bigint,
    runtime_contract character varying(64),
    manifest jsonb,
    entrypoints jsonb,
    status character varying(16) NOT NULL,
    error jsonb,
    validation_payload jsonb,
    build_claimed_at timestamp with time zone,
    builder_task_id character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    CONSTRAINT ck_agent_versions_digest CHECK (((image_digest)::text ~ '^sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_agent_versions_source_type CHECK (((source_type)::text = 'OCI_IMAGE'::text)),
    CONSTRAINT ck_agent_versions_status CHECK (((status)::text = ANY ((ARRAY['BUILDING'::character varying, 'PUBLISHED'::character varying, 'FAILED'::character varying])::text[]))),
    CONSTRAINT ck_agent_versions_version_id CHECK (((version_id)::text ~ '^[0-9a-f]{12}$'::text))
);


--
-- Name: agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agents (
    id uuid NOT NULL,
    owner_id uuid,
    name character varying(255) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: egress_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.egress_calls (
    call_id uuid NOT NULL,
    run_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    kind character varying(8) NOT NULL,
    rule_id uuid,
    method character varying(8),
    host character varying(253) NOT NULL,
    path text NOT NULL,
    status_code integer,
    request_bytes integer DEFAULT 0 NOT NULL,
    response_bytes integer DEFAULT 0 NOT NULL,
    latency_ms integer,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_egress_calls_kind CHECK (((kind)::text = ANY ((ARRAY['HTTP'::character varying, 'EMAIL'::character varying])::text[])))
);


--
-- Name: llm_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_calls (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    model character varying(128) NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6) DEFAULT '0'::numeric NOT NULL,
    latency_ms integer,
    status_code integer,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    credential_source character varying(16) DEFAULT 'PLATFORM'::character varying NOT NULL,
    CONSTRAINT ck_llm_calls_cost CHECK ((cost_usd >= (0)::numeric)),
    CONSTRAINT ck_llm_calls_credential_source CHECK (((credential_source)::text = ANY ((ARRAY['PLATFORM'::character varying, 'AGENT'::character varying])::text[]))),
    CONSTRAINT ck_llm_calls_input_tokens CHECK ((input_tokens >= 0)),
    CONSTRAINT ck_llm_calls_output_tokens CHECK ((output_tokens >= 0))
);


--
-- Name: run_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.run_events (
    seq bigint NOT NULL,
    run_id uuid NOT NULL,
    type character varying(32) NOT NULL,
    payload jsonb NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    event_id uuid NOT NULL,
    terminal boolean DEFAULT false NOT NULL,
    published_at timestamp with time zone
);


--
-- Name: run_events_seq_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.run_events_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: run_events_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.run_events_seq_seq OWNED BY public.run_events.seq;


--
-- Name: runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runs (
    id uuid NOT NULL,
    agent_id uuid NOT NULL,
    agent_version_id uuid,
    owner_id character varying(64),
    status character varying(16) NOT NULL,
    trigger character varying(16) NOT NULL,
    entrypoint character varying(128) NOT NULL,
    celery_task_id character varying(64),
    sandbox_id character varying(80),
    input jsonb NOT NULL,
    output jsonb,
    error jsonb,
    usage jsonb,
    timeout_seconds integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    result_received_at timestamp with time zone,
    api_key_id uuid,
    session_id uuid,
    container_id uuid,
    CONSTRAINT ck_runs_status CHECK (((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'SCHEDULING'::character varying, 'RUNNING'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'CANCELLED'::character varying])::text[]))),
    CONSTRAINT ck_runs_timeout CHECK (((timeout_seconds >= 1) AND (timeout_seconds <= 900))),
    CONSTRAINT ck_runs_trigger CHECK (((trigger)::text = ANY ((ARRAY['API'::character varying, 'MCP'::character varying])::text[])))
);


--
-- Name: run_events seq; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_events ALTER COLUMN seq SET DEFAULT nextval('public.run_events_seq_seq'::regclass);


--
-- Name: agent_api_keys agent_api_keys_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_api_keys
    ADD CONSTRAINT agent_api_keys_key_hash_key UNIQUE (key_hash);


--
-- Name: agent_api_keys agent_api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_api_keys
    ADD CONSTRAINT agent_api_keys_pkey PRIMARY KEY (id);


--
-- Name: agent_containers agent_containers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_containers
    ADD CONSTRAINT agent_containers_pkey PRIMARY KEY (id);


--
-- Name: agent_egress_rules agent_egress_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_egress_rules
    ADD CONSTRAINT agent_egress_rules_pkey PRIMARY KEY (rule_id);


--
-- Name: agent_email_sender agent_email_sender_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_email_sender
    ADD CONSTRAINT agent_email_sender_pkey PRIMARY KEY (agent_id);


--
-- Name: agent_llm_credentials agent_llm_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_llm_credentials
    ADD CONSTRAINT agent_llm_credentials_pkey PRIMARY KEY (agent_id);


--
-- Name: agent_secrets agent_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_secrets
    ADD CONSTRAINT agent_secrets_pkey PRIMARY KEY (agent_id, name);


--
-- Name: agent_settings agent_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_settings
    ADD CONSTRAINT agent_settings_pkey PRIMARY KEY (agent_id);


--
-- Name: agent_versions agent_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_versions
    ADD CONSTRAINT agent_versions_pkey PRIMARY KEY (id);


--
-- Name: agents agents_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_name_key UNIQUE (name);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);


--
-- Name: egress_calls egress_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.egress_calls
    ADD CONSTRAINT egress_calls_pkey PRIMARY KEY (call_id);


--
-- Name: llm_calls llm_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_pkey PRIMARY KEY (id);


--
-- Name: run_events run_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_events
    ADD CONSTRAINT run_events_pkey PRIMARY KEY (seq);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);


--
-- Name: agent_egress_rules uq_agent_egress_rules_agent_host; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_egress_rules
    ADD CONSTRAINT uq_agent_egress_rules_agent_host UNIQUE (agent_id, host);


--
-- Name: agent_versions uq_agent_versions_agent_semver; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_versions
    ADD CONSTRAINT uq_agent_versions_agent_semver UNIQUE (agent_id, semver);


--
-- Name: run_events uq_run_events_run_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_events
    ADD CONSTRAINT uq_run_events_run_event UNIQUE (run_id, event_id);


--
-- Name: ix_agent_api_keys_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_api_keys_agent ON public.agent_api_keys USING btree (agent_id);


--
-- Name: ix_agent_containers_agent_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_containers_agent_status ON public.agent_containers USING btree (agent_id, status);


--
-- Name: ix_agent_egress_rules_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_egress_rules_agent ON public.agent_egress_rules USING btree (agent_id);


--
-- Name: ix_agent_versions_agent_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_versions_agent_status_created ON public.agent_versions USING btree (agent_id, status, created_at DESC);


--
-- Name: ix_egress_calls_agent_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_egress_calls_agent_created ON public.egress_calls USING btree (agent_id, created_at DESC);


--
-- Name: ix_egress_calls_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_egress_calls_run ON public.egress_calls USING btree (run_id);


--
-- Name: ix_llm_calls_run_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_calls_run_created ON public.llm_calls USING btree (run_id, created_at);


--
-- Name: ix_run_events_run_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_run_events_run_seq ON public.run_events USING btree (run_id, seq);


--
-- Name: ix_runs_agent_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_agent_status_created ON public.runs USING btree (agent_id, status, created_at DESC);


--
-- Name: ix_runs_api_key_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_api_key_created ON public.runs USING btree (api_key_id, created_at);


--
-- Name: uq_run_events_terminal; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_run_events_terminal ON public.run_events USING btree (run_id) WHERE terminal;


--
-- Name: agent_api_keys agent_api_keys_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_api_keys
    ADD CONSTRAINT agent_api_keys_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_containers agent_containers_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_containers
    ADD CONSTRAINT agent_containers_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_containers agent_containers_agent_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_containers
    ADD CONSTRAINT agent_containers_agent_version_id_fkey FOREIGN KEY (agent_version_id) REFERENCES public.agent_versions(id);


--
-- Name: agent_egress_rules agent_egress_rules_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_egress_rules
    ADD CONSTRAINT agent_egress_rules_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_email_sender agent_email_sender_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_email_sender
    ADD CONSTRAINT agent_email_sender_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_llm_credentials agent_llm_credentials_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_llm_credentials
    ADD CONSTRAINT agent_llm_credentials_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_secrets agent_secrets_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_secrets
    ADD CONSTRAINT agent_secrets_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_settings agent_settings_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_settings
    ADD CONSTRAINT agent_settings_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_versions agent_versions_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_versions
    ADD CONSTRAINT agent_versions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: egress_calls egress_calls_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.egress_calls
    ADD CONSTRAINT egress_calls_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: egress_calls egress_calls_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.egress_calls
    ADD CONSTRAINT egress_calls_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.agent_egress_rules(rule_id) ON DELETE SET NULL;


--
-- Name: egress_calls egress_calls_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.egress_calls
    ADD CONSTRAINT egress_calls_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;


--
-- Name: runs fk_runs_agent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT fk_runs_agent FOREIGN KEY (agent_id) REFERENCES public.agents(id);


--
-- Name: runs fk_runs_agent_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT fk_runs_agent_version FOREIGN KEY (agent_version_id) REFERENCES public.agent_versions(id);


--
-- Name: llm_calls llm_calls_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;


--
-- Name: run_events run_events_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_events
    ADD CONSTRAINT run_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;


--
-- Name: runs runs_api_key_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_api_key_id_fkey FOREIGN KEY (api_key_id) REFERENCES public.agent_api_keys(id) ON DELETE SET NULL;


--
-- Name: runs runs_container_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_container_id_fkey FOREIGN KEY (container_id) REFERENCES public.agent_containers(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict MMZCdDT3w5LssEAUDiH2OJn4zemJl2q3C7YbxraMOPSD4XVprsabCpPhdAyXPy9

