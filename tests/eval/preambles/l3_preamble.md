CodeIR L3 token format:
TYPE ENTITY_ID [#DOMAIN] #CATEGORY

Entity IDs:
- Format: STEM or STEM.XX (e.g., AUTH, RDTKN.03)
- Type prefix is shown separately (TYPE ENTITY_ID)
- Full stable ID = TYPE.STEM.SUFFIX (e.g., AMT.RDTKN.03)

Domain tags (examples):
- #HTTP = HTTP/networking
- #AUTH = Authentication
- #CRYP = Cryptography
- #DB = Database
- #FS = File system
- #CLI = Command-line tooling
- #ASYN = Async/concurrency
- #PARS = Parsing/encoding
- #NET = Low-level networking

Category tags (examples):
- #CORE = Core logic
- #EXCE = Error/exception handling
- #TEST = Test code
- #UTIL = Utility code
