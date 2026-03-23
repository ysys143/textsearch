/*
 * korean_bigram.c  —  Phase 2-C: Custom PostgreSQL text search parser
 *
 * Strategy: Korean syllables (AC00-D7A3, Jamo) → individual syllable tokens.
 *           Latin/digit sequences → whole word tokens.
 *           Everything else skipped.
 *
 * Simple and correct: no static state, bigram handled by GIN index overlapping.
 * Individual Korean syllable tokens are combined by ts_rank / @@ operator
 * automatically.  Equivalent to unigram bigram for recall purposes.
 */

#include "postgres.h"
#include "fmgr.h"
#include "tsearch/ts_public.h"

PG_MODULE_MAGIC;

#define TOK_WORD    1
#define TOK_SYLLABLE 2

typedef struct {
    const char *str;
    int         len;
    int         pos;
} KParserState;

static int utf8_char_len(unsigned char c) {
    if (c < 0x80)           return 1;
    if ((c & 0xE0) == 0xC0) return 2;
    if ((c & 0xF0) == 0xE0) return 3;
    if ((c & 0xF8) == 0xF0) return 4;
    return 1;
}

static uint32_t utf8_decode(const unsigned char *p, int *blen) {
    *blen = utf8_char_len(*p);
    switch (*blen) {
        case 1: return p[0];
        case 2: return ((p[0] & 0x1F) << 6)  | (p[1] & 0x3F);
        case 3: return ((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6)  | (p[2] & 0x3F);
        case 4: return ((p[0] & 0x07) << 18) | ((p[1] & 0x3F) << 12) | ((p[2] & 0x3F) << 6) | (p[3] & 0x3F);
    }
    return p[0];
}

static bool is_hangul(uint32_t cp) {
    /* Hangul syllables */
    if (cp >= 0xAC00 && cp <= 0xD7A3) return true;
    /* Hangul Jamo */
    if (cp >= 0x1100 && cp <= 0x11FF) return true;
    /* Hangul Compatibility Jamo */
    if (cp >= 0x3130 && cp <= 0x318F) return true;
    return false;
}

static bool is_alnum(uint32_t cp) {
    return (cp >= 'A' && cp <= 'Z') || (cp >= 'a' && cp <= 'z') ||
           (cp >= '0' && cp <= '9');
}

PG_FUNCTION_INFO_V1(kb_start);
PG_FUNCTION_INFO_V1(kb_gettoken);
PG_FUNCTION_INFO_V1(kb_end);
PG_FUNCTION_INFO_V1(kb_lextype);

Datum kb_start(PG_FUNCTION_ARGS) {
    char *str = (char *) PG_GETARG_POINTER(0);
    int   len = PG_GETARG_INT32(1);
    KParserState *s = (KParserState *) palloc0(sizeof(KParserState));
    s->str = str;
    s->len = len;
    s->pos = 0;
    PG_RETURN_POINTER(s);
}

Datum kb_gettoken(PG_FUNCTION_ARGS) {
    KParserState    *s    = (KParserState *)    PG_GETARG_POINTER(0);
    char           **t    = (char **)           PG_GETARG_POINTER(1);
    int             *tlen = (int *)             PG_GETARG_POINTER(2);

    const unsigned char *p = (const unsigned char *) s->str;

    while (s->pos < s->len) {
        int      blen;
        uint32_t cp = utf8_decode(p + s->pos, &blen);

        if (is_hangul(cp)) {
            /* Emit single syllable */
            *t    = (char *) s->str + s->pos;
            *tlen = blen;
            s->pos += blen;
            PG_RETURN_INT32(TOK_SYLLABLE);
        }

        if (is_alnum(cp)) {
            int start = s->pos;
            while (s->pos < s->len) {
                uint32_t c2 = utf8_decode(p + s->pos, &blen);
                if (!is_alnum(c2) && c2 != '-' && c2 != '_') break;
                s->pos += blen;
            }
            if (s->pos > start) {
                *t    = (char *) s->str + start;
                *tlen = s->pos - start;
                PG_RETURN_INT32(TOK_WORD);
            }
        }

        s->pos += blen;  /* skip */
    }

    PG_RETURN_INT32(0);
}

Datum kb_end(PG_FUNCTION_ARGS) {
    KParserState *s = (KParserState *) PG_GETARG_POINTER(0);
    pfree(s);
    PG_RETURN_VOID();
}

Datum kb_lextype(PG_FUNCTION_ARGS) {
    LexDescr *d = (LexDescr *) palloc(sizeof(LexDescr) * 3);
    d[0].lexid = TOK_WORD;
    d[0].alias = pstrdup("word");
    d[0].descr = pstrdup("Latin/digit word");
    d[1].lexid = TOK_SYLLABLE;
    d[1].alias = pstrdup("syllable");
    d[1].descr = pstrdup("Korean syllable (unigram)");
    d[2].lexid = 0;
    PG_RETURN_POINTER(d);
}
