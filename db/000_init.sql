-- Схема контура «AI-чат в банковском приложении».
-- Применяется целиком при подготовке стенда (make migrate); обратных миграций нет.

create extension if not exists vector;

-- ── Демо-пользователи контура (субъекты JWT) ─────────────────────────────
create table if not exists users(
    login   text primary key,
    sub     text unique not null,
    name    text not null,
    role    text not null,
    scopes  text[] not null
);

-- ── Домен банка: клиенты, счета, карты, лимиты, операции, комиссии ────────
create table if not exists clients(
    sub    text primary key,
    login  text unique not null references users(login),
    name   text not null,
    phone  text,
    email  text
);

create table if not exists accounts(
    account_id text primary key,
    sub        text not null,
    kind       text not null,             -- расчётный | накопительный
    title      text not null,
    balance    numeric(12,2) not null default 0,
    currency   text not null default 'RUB'
);

create table if not exists cards(
    card_id    text primary key,
    sub        text not null,
    account_id text not null references accounts(account_id),
    mask       text not null,             -- последние 4 цифры: «4417»
    product    text not null,
    status     text not null default 'active',   -- active | blocked
    expires    text not null              -- «08/29»
);

create table if not exists card_limits(
    card_id       text primary key references cards(card_id),
    daily_limit   numeric(12,2) not null,
    monthly_limit numeric(12,2) not null
);

create table if not exists transactions(
    txn_id      text primary key,
    sub         text not null,
    account_id  text not null references accounts(account_id),
    ts          timestamptz not null,
    amount      numeric(12,2) not null,   -- всегда положительная величина
    direction   text not null,            -- in (поступление) | out (списание)
    merchant    text not null,
    category    text not null,
    description text not null default '',
    status      text not null default 'completed'  -- completed | declined
);

create table if not exists commission_rules(
    rule_id     text primary key,
    product     text not null,            -- «все» | название продукта
    code        text not null,
    description text not null,
    amount_rub  numeric(10,2)
);

create table if not exists tickets(
    id         text primary key,
    sub        text not null,
    subject    text not null,
    note       text not null,
    status     text not null default 'open',   -- open | in_progress | resolved
    created_at timestamptz not null default now()
);

-- ── База знаний: чанки + векторы (снапшот считается заранее) ─────────────
create table if not exists kb_chunks(
    id        bigserial primary key,
    source    text not null,
    title     text not null,
    body      text not null,
    embedding vector(768)
);

-- ── Контур: сессии, карточки подтверждений, идемпотентность ──────────────
create table if not exists sessions(
    session_id text primary key,
    login      text not null,
    sub        text not null,
    thread     jsonb not null default '[]',
    created_at timestamptz not null default now()
);

create table if not exists pending_cards(
    pending_id text primary key,
    session_id text not null,
    sub        text not null,
    action     text not null,
    details    jsonb not null,
    card       jsonb not null,
    state      text not null default 'pending',
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create table if not exists idempotency(
    key        text primary key,
    sub        text not null,
    action     text not null,
    result     jsonb,
    created_at timestamptz not null default now()
);

-- ── Журналы: аудит, LLM-вызовы, tool-вызовы, решения политики ────────────
create table if not exists audit_log(
    id              bigserial primary key,
    ts              timestamptz not null default now(),
    trace_id        text,
    session_id      text,
    who             text not null,
    what            text not null,
    outcome         text not null,
    args            jsonb,
    before          jsonb,
    after           jsonb,
    idempotency_key text,
    detail          text
);

create table if not exists llm_calls(
    id            bigserial primary key,
    ts            timestamptz not null default now(),
    trace_id      text,
    agent         text,
    model         text,
    provider      text not null default 'primary',
    model_version text not null default '',
    input_tokens  int,
    output_tokens int,
    latency_ms    int
);

create table if not exists tool_calls(
    id         bigserial primary key,
    ts         timestamptz not null default now(),
    trace_id   text,
    tool       text not null,
    args       jsonb,
    outcome    text,
    latency_ms int
);

create table if not exists policy_events(
    id         bigserial primary key,
    ts         timestamptz not null default now(),
    session_id text,
    sub        text,
    action     text not null,
    decision   text not null,
    reason     text
);
