uv run python main.py \
 --file java_repos/micronaut-core/inject-java/src/test/groovy/io/micronaut/inject/configuration/ExternalConfigurationImport.java \
 --workspace-json /tmp/micronaut-workspace-model.json

If you also want external dependency resolution in the demo output:

uv run python main.py \
 --file java_repos/micronaut-core/inject-java/src/test/groovy/io/micronaut/inject/configuration/ExternalConfigurationImport.java \
 --workspace-json /tmp/micronaut-workspace-model.json \
 --resolve-jars

uv run python -m codelens index -v workspace \
 --repo-root /Users/vedangbarhate/Desktop/workspace/micronaut-core \
 --workspace-json /Users/vedangbarhate/Desktop/workspace/micronaut-core/workspace.json \
 --device mps \
 --batch-size 16
