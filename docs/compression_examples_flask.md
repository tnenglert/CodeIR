# CodeIR Compression Examples (Flask Repository)

This document shows 10 entities from the Flask repository at all three compression levels.

## Compression Levels

| Level | Purpose | Content |
|-------|---------|---------|
| **Index** | Orientation/selection | `TYPE ID #DOMAIN #CAT` — minimal, ~6-13 tokens |
| **Behavior** | Task relevance | `TYPE ID C=calls F=flags A=assignments #DOMAIN #CAT` — behavioral, ~15-30 tokens |
| **Source** | Pre-action verification | `[TYPE ID @path:line]` + full source |

## Behavior Field Reference

- `C=` calls made (abbreviated)
- `F=` flags: `R`=returns value, `E`=raises exception, `I`=has conditionals, `L`=loops, `T`=try/except, `W`=with
- `A=` assignment count
- `B=` base class (for classes)
- `#DOMAIN` = domain tag (AUTH, HTTP, FS, PARSE, CLI, DB, CRYPTO, etc.) — omitted if unknown
- `#CAT` = category tag (CORE, TEST, ROUT, CONF, EXCE, etc.)

---

## Example 1: login_required

**Location:** `examples/tutorial/flaskr/auth.py:19-29`

### Index (Sparse Orientation)
```
FN LGNRQRD #AUTH #ROUT
```

### Behavior (Behavioral Navigation)
```
FN LGNRQRD C=RDIR,url_for,view,wraps F=IR #AUTH #ROUT
```

### Source (Raw Source)
```python
[FN LGNRQRD @examples/tutorial/flaskr/auth.py:19]
def login_required(view):
    """View decorator that redirects anonymous users to the login page."""

    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))

        return view(**kwargs)

    return wrapped_view
```

---

## Example 2: Blueprint

**Location:** `src/flask/blueprints.py:18-128`

### Index (Sparse Orientation)
```
CLS BLPRNT #CORE
```

### Behavior (Behavioral Navigation)
```
CLS BLPRNT C=AppGroup,RuntimeError,SansioBlueprint,ValueError,CINIT,cast F=EIR A=5 B=SansioBlueprint #CORE
```

### Source (Raw Source)
```python
[CLS BLPRNT @src/flask/blueprints.py:18]
class Blueprint(SansioBlueprint):
    def __init__(
        self,
        name: str,
        import_name: str,
        static_folder: str | os.PathLike[str] | None = None,
        static_url_path: str | None = None,
        template_folder: str | os.PathLike[str] | None = None,
        url_prefix: str | None = None,
        subdomain: str | None = None,
        url_defaults: dict[str, t.Any] | None = None,
        root_path: str | None = None,
        cli_group: str | None = _sentinel,  # type: ignore
    ) -> None:
        super().__init__(
            name,
            import_name,
            static_folder,
            static_url_path,
            template_folder,
            url_prefix,
            subdomain,
            url_defaults,
            root_path,
            cli_group,
        )

        #: The Click command group for registering CLI commands for this
        #: object. The commands are available from the ``flask`` command
        #: once the application has been discovered and blueprints have
        #: been registered.
        self.cli = AppGroup()

        # Set the name of the Click group in case someone wants to add
        # the app's commands to another CLI tool.
        self.cli.name = self.name

    def get_send_file_max_age(self, filename: str | None) -> int | None:
        ...

    def send_static_file(self, filename: str) -> Response:
        ...

    def open_resource(
        self, resource: str, mode: str = "rb", encoding: str | None = "utf-8"
    ) -> t.IO[t.AnyStr]:
        ...
```

---

## Example 3: JSONProvider.response

**Location:** `src/flask/json/provider.py:89-105`

### Index (Sparse Orientation)
```
MT RSPNS #PARSE #CORE
```

### Behavior (Behavioral Navigation)
```
MT RSPNS C=_prepare_response_obj,dumps,response_class F=R A=1 #PARSE #CORE
```

### Source (Raw Source)
```python
[MT RSPNS @src/flask/json/provider.py:89]
    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """Serialize the given arguments as JSON, and return a
        :class:`~flask.Response` object with the ``application/json``
        mimetype.
        """
        obj = self._prepare_response_obj(args, kwargs)
        return self._app.response_class(self.dumps(obj), mimetype="application/json")
```

---

## Example 4: Scaffold.url_defaults

**Location:** `src/flask/sansio/scaffold.py:584-595`

### Index (Sparse Orientation)
```
MT URLDFLTS #CORE
```

### Behavior (Behavioral Navigation)
```
MT URLDFLTS C=append F=R #CORE
```

### Source (Raw Source)
```python
[MT URLDFLTS @src/flask/sansio/scaffold.py:584]
    def url_defaults(self, f: T_url_defaults) -> T_url_defaults:
        """Callback function for URL defaults for all view functions of the
        application. It's called with the endpoint and values and should
        update the values passed in place.
        """
        self.url_default_functions[None].append(f)
        return f
```

---

## Example 5: _split_blueprint_path

**Location:** `src/flask/helpers.py:645-651`

### Index (Sparse Orientation)
```
FN SPLTBLPRNTPTH #CORE
```

### Behavior (Behavioral Navigation)
```
FN SPLTBLPRNTPTH C=_split_blueprint_path,extend,rpartition F=IR A=1 #CORE
```

### Source (Raw Source)
```python
[FN SPLTBLPRNTPTH @src/flask/helpers.py:645]
def _split_blueprint_path(name: str) -> list[str]:
    out: list[str] = [name]

    if "." in name:
        out.extend(_split_blueprint_path(name.rpartition(".")[0]))

    return out
```

---

## Example 6: Scaffold.errorhandler

**Location:** `src/flask/sansio/scaffold.py:598-639`

### Index (Sparse Orientation)
```
MT ERRRHNDLR #CORE
```

### Behavior (Behavioral Navigation)
```
MT ERRRHNDLR C=register_error_handler F=R #CORE
```

### Source (Raw Source)
```python
[MT ERRRHNDLR @src/flask/sansio/scaffold.py:598]
    def errorhandler(
        self, code_or_exception: type[Exception] | int
    ) -> t.Callable[[T_error_handler], T_error_handler]:
        """Register a function to handle errors by code or exception class."""

        def decorator(f: T_error_handler) -> T_error_handler:
            self.register_error_handler(code_or_exception, f)
            return f

        return decorator
```

---

## Example 7: AppContext.request

**Location:** `src/flask/ctx.py:371-379`

### Index (Sparse Orientation)
```
MT RQST #CORE
```

### Behavior (Behavioral Navigation)
```
MT RQST C=RuntimeError F=EIR #CORE
```

### Source (Raw Source)
```python
[MT RQST @src/flask/ctx.py:371]
    def request(self) -> Request:
        """The request object associated with this context."""
        if self._request is None:
            raise RuntimeError("There is no request in this context.")

        return self._request
```

---

## Example 8: test_basic_view

**Location:** `tests/test_views.py:18-26`

### Index (Sparse Orientation)
```
FN TSTBSCVW #TEST
```

### Behavior (Behavioral Navigation)
```
FN TSTBSCVW C=add_url_rule,as_view,common_test F=R A=1 #TEST
```

### Source (Raw Source)
```python
[FN TSTBSCVW @tests/test_views.py:18]
def test_basic_view(app):
    class Index(flask.views.View):
        methods = ["GET", "POST"]

        def dispatch_request(self):
            return flask.request.method

    app.add_url_rule("/", view_func=Index.as_view("index"))
    common_test(app)
```

---

## Example 9: test_iterable_loader

**Location:** `tests/test_templating.py:423-440`

### Index (Sparse Orientation)
```
FN TSTTRBLLDR #TEST
```

### Behavior (Behavioral Navigation)
```
FN TSTTRBLLDR C=get,render_template,route F=R A=1 #TEST
```

### Source (Raw Source)
```python
[FN TSTTRBLLDR @tests/test_templating.py:423]
def test_iterable_loader(app, client):
    @app.context_processor
    def context_processor():
        return {"whiskey": "Jameson"}

    @app.route("/")
    def index():
        return flask.render_template(
            [
                "no_template.xml",  # should skip this one
                "simple_template.html",  # should render this
                "context_template.html",
            ],
            value=23,
        )

    rv = client.get("/")
    assert rv.data == b"<h1>Jameson</h1>"
```

---

## Example 10: Flask.send_static_file

**Location:** `src/flask/app.py:392-412`

### Index (Sparse Orientation)
```
MT SNDSTTCFL #CORE
```

### Behavior (Behavioral Navigation)
```
MT SNDSTTCFL C=RuntimeError,cast,get_send_file_max_age,send_from_directory F=EIR A=1 #CORE
```

### Source (Raw Source)
```python
[MT SNDSTTCFL @src/flask/app.py:392]
    def send_static_file(self, filename: str) -> Response:
        """The view function used to serve files from
        :attr:`static_folder`.
        """
        if not self.has_static_folder:
            raise RuntimeError("'static_folder' must be set to serve static_files.")

        max_age = self.get_send_file_max_age(filename)
        return send_from_directory(
            t.cast(str, self.static_folder), filename, max_age=max_age
        )
```
