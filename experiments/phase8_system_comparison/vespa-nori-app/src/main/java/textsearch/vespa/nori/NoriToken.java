package textsearch.vespa.nori;

import com.yahoo.language.process.Token;
import com.yahoo.language.process.TokenScript;
import com.yahoo.language.process.TokenType;

/**
 * Token implementation for Nori tokenizer output.
 */
public class NoriToken implements Token {

    private final String orig;
    private final String tokenString;
    private final long offset;

    public NoriToken(String orig, String tokenString, long offset) {
        this.orig = orig;
        this.tokenString = tokenString;
        this.offset = offset;
    }

    @Override
    public TokenType getType() {
        return TokenType.ALPHABETIC;
    }

    @Override
    public String getOrig() {
        return orig;
    }

    @Override
    public int getNumStems() {
        return tokenString != null ? 1 : 0;
    }

    @Override
    public String getStem(int i) {
        return tokenString;
    }

    @Override
    public int getNumComponents() {
        return 0;
    }

    @Override
    public Token getComponent(int i) {
        return null;
    }

    @Override
    public long getOffset() {
        return offset;
    }

    @Override
    public TokenScript getScript() {
        return TokenScript.HANGUL;
    }

    @Override
    public String getTokenString() {
        return tokenString;
    }

    @Override
    public boolean isSpecialToken() {
        return false;
    }

    @Override
    public boolean isIndexable() {
        return getType().isIndexable() && orig.length() > 0;
    }
}
