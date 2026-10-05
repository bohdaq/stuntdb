# Format-preserving providers

Use provider action names in `rules` in a reviewed config. They are explicit replacements for token/keep/clear; existing auto rules keep the previous conservative behavior. New init configs suggest email, phone, name and birth-date providers from recognizable column names. Numeric providers require explicit review.

| Action | Accepted storage | Output |
| --- | --- | --- |
| email | Ordinary string | HMAC-derived local part at example.invalid, within the shortest linked column width |
| phone | Ordinary string with ASCII digits and +, parentheses, spaces, periods or hyphens | Same digit positions and separators, with deterministic replacement digits |
| name | Ordinary string with letters, whitespace, apostrophes, hyphens or periods | ASCII replacement letters preserving character count, case pattern and separators |
| date | Native DATE | A deterministic valid calendar date from 1940-01-01 through 2005-12-31 |
| integer | Integer | Same magnitude digit count and negative/nonnegative category, within the intersection of declared integer ranges |
| number | Fixed-precision DECIMAL | Same scaled magnitude digit count, sign category and declared fractional scale, within precision and unsigned bounds |

Example rules:

```json
{
  "people.email": "email",
  "people.phone": "phone",
  "people.full_name": "name",
  "people.birth_date": "date",
  "people.ssn": "integer",
  "payments.amount": "number"
}
```

An action propagates to the whole relationship domain. A narrower linked string limits all outputs, and linked decimal columns must declare identical precision and scale. NULL is retained; empty email, phone and name strings remain empty. Collisions, invalid input shapes, original values surviving, unsupported types and unique-index prefix conflicts fail before output. Algorithms use HMAC-SHA256 and the project salt; there is no remote service, random seed or provider library version dependency. Changing the action changes outputs; existing token output is unchanged.

These preserve storage formats, not application semantics: dates do not preserve age or ordering, numbers do not preserve arithmetic relationships, names are pseudonyms rather than realistic names, and phone digit patterns do not establish regional validity or prevent matching a real number. Email columns must fit at least one local character plus @example.invalid. Very short names and numeric ranges can collide or reproduce a source value and fail closed. Numeric CHECK constraints beyond standard storage ranges remain the user's responsibility. Unicode letters become ASCII; combining marks and unrecognized name/phone shapes fail rather than being passed through. Date strings, DATETIME/TIMESTAMP, floating-point fields, enum/SET and spatial fields do not use these providers.

The masked Employees zoo now masks birth dates rather than explicitly retaining them. Sakila exercises masked email/name/phone values; enum/SET and spatial values are still explicitly kept in sample-test rules. These tests do not claim full anonymization.
