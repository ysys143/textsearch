\echo Use "CREATE EXTENSION korean_bigram" to load this file. \quit

CREATE FUNCTION kb_start(internal, int4)
    RETURNS internal AS 'MODULE_PATHNAME' LANGUAGE C STRICT;

CREATE FUNCTION kb_gettoken(internal, internal, internal)
    RETURNS internal AS 'MODULE_PATHNAME' LANGUAGE C STRICT;

CREATE FUNCTION kb_end(internal)
    RETURNS void AS 'MODULE_PATHNAME' LANGUAGE C STRICT;

CREATE FUNCTION kb_lextype(internal)
    RETURNS internal AS 'MODULE_PATHNAME' LANGUAGE C STRICT;

CREATE TEXT SEARCH PARSER korean_bigram_parser (
    START    = kb_start,
    GETTOKEN = kb_gettoken,
    END      = kb_end,
    LEXTYPES = kb_lextype,
    HEADLINE = pg_catalog.prsd_headline
);

CREATE TEXT SEARCH CONFIGURATION korean_bigram (
    PARSER = korean_bigram_parser
);

ALTER TEXT SEARCH CONFIGURATION korean_bigram
    ADD MAPPING FOR syllable, word WITH simple;
