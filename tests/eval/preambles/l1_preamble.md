SemanticIR is a compressed representation of Python code entities.

Entity types:
- MT=method
- AMT=async method
- FN=function
- AFN=async function
- CLS=class

Entity IDs:
- Format: STEM or STEM.XX (e.g., AUTH, RDTKN.03)
- Type prefix is shown separately (TYPE ENTITY_ID)
- Full stable ID = TYPE.STEM.SUFFIX (e.g., AMT.RDTKN.03)
- Think of it like phone numbers: area code (type) + number (stem.suffix)

L1 format:
TYPE ENTITY_ID [C=<calls>] [F=<flags>] [A=<assign_count>] [B=<bases>] [#DOMAIN] [#CATE]

Fields (omitted when empty/zero):
- C: semantic references (calls and class inheritance refs) — absent if no calls
- F: behavioral flags — absent if no flags apply
- A: assignment density (count of assignment operations) — absent if zero
- B: class base references — absent if no base classes

Behavioral flags:
- A=await encountered
- E=raises
- I=conditionals
- L=loops
- R=returns
- T=try/except
- W=with-context
- X=exception-type class

Optional tags:
- #HTTP/#AUTH/#CRYP/#DB/#FS/#CLI/#ASYN/#PARS/#NET = domain
- #CORE/#ROUT/#SCHE/#CONF/#COMP/#EXCE/#CONS/#TEST/#INIT/#DOCS/#UTIL = category

Examples:
CLS TMT C=RequestException F=X B=RequestException #HTTP #EXCE
MT GET F=R #HTTP #CORE
FN HLPR #UTIL
