package textsearch.vespa.nori;

import com.yahoo.language.Language;
import com.yahoo.language.process.StemMode;
import com.yahoo.language.process.Token;
import com.yahoo.language.process.Tokenizer;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.ko.KoreanAnalyzer;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.analysis.tokenattributes.OffsetAttribute;

import java.io.IOException;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Vespa Tokenizer wrapping Lucene's KoreanAnalyzer (Nori).
 * For Korean text, uses morphological analysis via Nori.
 * For other languages, falls back to SimpleLinguistics tokenizer.
 */
public class NoriTokenizer implements Tokenizer {

    private final Analyzer koreanAnalyzer;
    private final Tokenizer fallback;

    public NoriTokenizer(Tokenizer fallback) {
        this.koreanAnalyzer = new KoreanAnalyzer();
        this.fallback = fallback;
    }

    @Override
    public Iterable<Token> tokenize(String input, Language language, StemMode stemMode, boolean removeAccents) {
        if (input == null || input.isEmpty()) {
            return Collections.emptyList();
        }

        if (language != Language.KOREAN && language != Language.UNKNOWN) {
            return fallback.tokenize(input, language, stemMode, removeAccents);
        }

        List<Token> tokens = new ArrayList<>();
        try (TokenStream stream = koreanAnalyzer.tokenStream("text", new StringReader(input))) {
            CharTermAttribute termAttr = stream.addAttribute(CharTermAttribute.class);
            OffsetAttribute offsetAttr = stream.addAttribute(OffsetAttribute.class);
            stream.reset();

            while (stream.incrementToken()) {
                String term = termAttr.toString();
                if (term.isEmpty()) continue;

                int startOffset = offsetAttr.startOffset();
                int endOffset = offsetAttr.endOffset();
                String orig = (startOffset >= 0 && endOffset <= input.length() && startOffset < endOffset)
                        ? input.substring(startOffset, endOffset)
                        : term;

                tokens.add(new NoriToken(orig, term, startOffset));
            }
            stream.end();
        } catch (Exception e) {
            // Log and fallback - catches OSGi classloader issues with Nori dictionary
            java.util.logging.Logger.getLogger("NoriTokenizer")
                .warning("Nori tokenization failed for: " + input.substring(0, Math.min(30, input.length()))
                         + " error: " + e.getClass().getName() + ": " + e.getMessage());
            return fallback.tokenize(input, language, stemMode, removeAccents);
        }

        // Log tokens for debugging
        java.util.logging.Logger logger = java.util.logging.Logger.getLogger("NoriTokenizer");
        StringBuilder sb = new StringBuilder("lang=" + language + " input=" + input.substring(0, Math.min(40, input.length())) + " → [");
        for (Token t : tokens) {
            sb.append(t.getTokenString()).append("(").append(t.getType()).append("),");
        }
        sb.append("]");
        logger.info(sb.toString());

        if (tokens.isEmpty()) {
            return fallback.tokenize(input, language, stemMode, removeAccents);
        }

        return tokens;
    }
}
