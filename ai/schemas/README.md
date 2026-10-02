# ai/schemas/ — generated, do not edit

`make schemas` populates this folder. It exports the JSON Schema of every Pydantic model an agent spec or tool spec points at: the output models and envelopes in `fcrm_contracts.proposals.*`, and the tool input and output models once they move into `fcrm_contracts.tools.*`. One file per model is written as `<ClassName>.schema.json`, using `model_json_schema(mode="serialization")`, with sorted keys so diffs are stable.

Nothing here is written by hand. A hand-written schema could drift from the model that actually validates the output, and the model is the source of truth.

Who reads these files:
- the evaluation panel, to see exactly what each agent must return;
- the web app's type generator, for the analyst views that render proposals.

The runtime never reads them. `fcrm_prompts_loader` renders `{{ schema("Name") }}` from the live Pydantic class, not from this folder.

Until `make schemas` exists, this folder contains only this README.
