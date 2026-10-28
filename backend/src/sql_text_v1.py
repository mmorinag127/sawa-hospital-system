"""SQL equivalent of Python's Unicode whitespace stripping."""
from sqlalchemy import String, Text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

WHITESPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


class PythonStrip(FunctionElement):
    type = Text()
    inherit_cache = True


@compiles(PythonStrip)
def _standard(element, compiler, **kwargs):
    return 'trim(BOTH ' + compiler.render_literal_value(WHITESPACE, String()) + ' FROM ' + compiler.process(element.clauses, **kwargs) + ')'


@compiles(PythonStrip, 'postgresql')
@compiles(PythonStrip, 'sqlite')
def _strip(element, compiler, **kwargs):
    function = 'btrim' if compiler.dialect.name == 'postgresql' else 'trim'
    return function + '(' + compiler.process(element.clauses, **kwargs) + ', ' + compiler.render_literal_value(WHITESPACE, String()) + ')'
