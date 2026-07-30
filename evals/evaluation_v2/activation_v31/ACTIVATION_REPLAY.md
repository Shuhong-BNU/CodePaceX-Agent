# V3.1 zero-provider activation replay

This is a deterministic development-set identity replay. It does not run a Provider, check out task repositories, execute tests, or calculate a resolved rate.

- Provider requests / usage / charge: `0 / 0 / CNY 0`
- Secret read: `false`
- Preserved repository-commit anchors: `20/20`
- Source-definition anchors: `0`; see the explicit no-checkout limitation in the JSON artifact.

| Task | Repository anchor | Entities | Unknown reason |
| --- | --- | --- | --- |
| aws-cloudformation__cfn-lint-3749 | aws-cloudformation/cfn-lint@07652d4a4133e825aeeb09973398575b25713c82 | ForEach, could, resolved, when, values, array | - |
| aws-cloudformation__cfn-lint-3764 | aws-cloudformation/cfn-lint@0d7df0385cfa566a29c2ba73188224fb15d93889 | ForEach, could, resolved, when, values, array | - |
| beetbox__beets-5457 | beetbox/beets@03f1205629ad17f123a190040361babc41c18afc | Tekstowo, backend, does, return, lyrics, more | - |
| beetbox__beets-5495 | beetbox/beets@fa10dcf11add0afd3b4b22af29f8d504e7ef8a0a | import.set_fields, configuration, should, tolerate, string, values | - |
| deepset-ai__haystack-8489 | deepset-ai/haystack@906177329bcc54f6946af361fcd3d0e334e6ce5f | Potential, breaking, change, Tracing, with, concurrency | - |
| beancount__beancount-931 | beancount/beancount@a0e6f445fbf0d101602a4b6d886d6320971587b6 | Allow, balance, check, directives, against, leaf | - |
| beeware__briefcase-2075 | beeware/briefcase@98b3cb01f6865550eb083646d3f9e4e5dfcfda82 | Poor, default, value, DBus, access, Flatpak | - |
| beeware__briefcase-2085 | beeware/briefcase@4005202304fdef04a5e87de2e8b09c9de506dcae | Briefcase, attempt, URL, origin, remote, templates | - |
| bridgecrewio__checkov-6893 | bridgecrewio/checkov@7741985fd08414d35f7aa75ec9e6ce65eee3b522 | Issue, with, Check, CKV2_AZURE_31, skipping, GatewaySubnet | - |
| bridgecrewio__checkov-6895 | bridgecrewio/checkov@a94c1682b275bbf755342c0164d2ac0c379c0c16 | CKV_AZURE_136, False, Positive, For, Read, Replicas | - |
| conan-io__conan-17092 | conan-io/conan@f31647f3ef2feaabc91b2875d540ae48d2a4a4c8 | feature, support, your, suggestion, clang, already | - |
| conan-io__conan-17102 | conan-io/conan@2e3f51782056b6665560f9af6166e30d7c2801ab | question, Profile, information, merged, build, order | - |
| cyclotruc__gitingest-115 | cyclotruc/gitingest@96bc3958a3b3409e009c80d7ac89a97c4c9520fa | injest, field, case, insensitive, type, Https | - |
| cyclotruc__gitingest-134 | cyclotruc/gitingest@8137ce10649526820efe752ff81eefabbea8ee23 | gitingest, fails, resolve, urls, starting, with | - |
| deepset-ai__haystack-8525 | deepset-ai/haystack@911f3523ab94472bd9a1f8ecbd2493437058daee | Support, splitting, CSV, documents, your, feature | - |
| delgan__loguru-1297 | Delgan/loguru@e310e2029102b5d63a679a2b64501c045aa86336 | Won, handle, future, time, system, clock | - |
| delgan__loguru-1306 | Delgan/loguru@3cfd03fb6fd2176b90ad14223408f3c4ec803cb6 | Support, FORCE_COLOR, https, force, color, Similar | - |
| dynaconf__dynaconf-1225 | dynaconf/dynaconf@39acdeef6424bf7e336ff71cf3a04540b92e2fcd | Ports, from, master, Insert, token, related | - |
| dynaconf__dynaconf-1249 | dynaconf/dynaconf@71ea887ade58f57cbc5b37f311188bfb7cda8ca5 | RFC, Allow, registering, hooks, settings, when | - |
| instructlab__instructlab-2540 | instructlab/instructlab@bcf450d0eb712309fa22fd23073ddfba51d575e8 | ilab, chat, should, allow, user, temperature | - |
