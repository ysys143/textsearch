package textsearch.vespa.nori;

import com.yahoo.language.Linguistics;
import com.yahoo.language.detect.Detector;
import com.yahoo.language.process.CharacterClasses;
import com.yahoo.language.process.GramSplitter;
import com.yahoo.language.process.Normalizer;
import com.yahoo.language.process.Segmenter;
import com.yahoo.language.process.SegmenterImpl;
import com.yahoo.language.process.Stemmer;
import com.yahoo.language.process.StemmerImpl;
import com.yahoo.language.process.Tokenizer;
import com.yahoo.language.process.Transformer;
import com.yahoo.language.simple.SimpleLinguistics;

/**
 * Vespa Linguistics implementation using Lucene Nori (Korean morphological analyzer).
 * Follows the same pattern as yahoojapan/vespa-kuromoji-linguistics.
 */
public class NoriLinguistics implements Linguistics {

    private final SimpleLinguistics simpleLinguistics = new SimpleLinguistics();
    private final Tokenizer tokenizer;

    public NoriLinguistics() {
        this.tokenizer = new NoriTokenizer(simpleLinguistics.getTokenizer());
    }

    @Override
    public Stemmer getStemmer() {
        return new StemmerImpl(getTokenizer());
    }

    @Override
    public Tokenizer getTokenizer() {
        return tokenizer;
    }

    @Override
    public Normalizer getNormalizer() {
        return simpleLinguistics.getNormalizer();
    }

    @Override
    public Transformer getTransformer() {
        return simpleLinguistics.getTransformer();
    }

    @Override
    public Segmenter getSegmenter() {
        return new SegmenterImpl(getTokenizer());
    }

    @Override
    public Detector getDetector() {
        return simpleLinguistics.getDetector();
    }

    @Override
    public GramSplitter getGramSplitter() {
        return simpleLinguistics.getGramSplitter();
    }

    @Override
    public CharacterClasses getCharacterClasses() {
        return simpleLinguistics.getCharacterClasses();
    }

    @Override
    public boolean equals(Linguistics other) {
        return (other instanceof NoriLinguistics);
    }
}
