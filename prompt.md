You will receive a JSON file containing one or more table items.

For each item in "tables":

Inspect input.html_snippet

Fill output.normalization_operations with zero or more values chosen from allowed_values.type_of_normalization_operations that you applied. If no normalization operation was applied, return an empty array [].

Fill output.flattened_schema with the final flat column names as an array of strings, e.g. ["Market", "Year", "Gross (Local)", "Gross (USD)", "Ticket Sales"]

Fill output.ground_truth.rows with an array of row objects where each key is a column from flattened_schema and each value is the exact cell text from the HTML, e.g. [{"Market": "Hong Kong", "Year": "1995", "Gross (USD)": "$7,356,820"}]

Rules:

Return the exact JSON structure you received — drop the input field from each table item and keep everything else (including allowed_values, output, and all other keys) unchanged

Only fill the null/empty fields in output — do not remove or rename any other keys

Cell values must reflect the clean, meaningful data content as it would appear in a relational database — copy the actual text from the HTML but strip any annotations or external references that do not belong as data in a relational table; no rephrasing, no conversion, no translation

The only permitted value transformation is normalize_missing_value_to_null: empty cells, "—", "N/A", or similar may be set to null — always include every row even if incomplete, filling missing cells with null

Do not use values outside allowed_values.type_of_normalization_operations

Do not add explanations or any text outside the JSON
