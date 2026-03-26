FROM vespaengine/vespa:latest

# Replace lucene-linguistics JAR with a patched version that includes
# lucene-analysis-nori-9.11.1.jar in dependencies/ and Bundle-ClassPath.
# This enables Korean morphological analysis (Nori) via Lucene Linguistics.
COPY lucene-linguistics-patched.jar /opt/vespa/lib/jars/lucene-linguistics-jar-with-dependencies.jar
