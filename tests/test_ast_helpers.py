"""Tests for ast_helpers.py — comment_metadata."""

from codelens.ast_helpers import comment_metadata


def test_none_input():
    result = comment_metadata(None)
    assert result == {"leading_comment": None, "javadoc": None}


def test_empty_string():
    result = comment_metadata("")
    assert result["leading_comment"] == ""
    assert result["javadoc"] is None


def test_line_comment():
    result = comment_metadata("// a comment")
    assert result["leading_comment"] == "// a comment"
    assert result["javadoc"] is None


def test_block_comment_not_javadoc():
    result = comment_metadata("/* block */")
    assert result["leading_comment"] == "/* block */"
    assert result["javadoc"] is None


def test_javadoc_only():
    result = comment_metadata("/** API docs. */")
    assert result["leading_comment"] == "/** API docs. */"
    assert result["javadoc"] == "/** API docs. */"


def test_multiline_javadoc():
    comment = "/**\n * Processes an order.\n * @param id order id\n */"
    result = comment_metadata(comment)
    assert result["javadoc"] == comment


def test_mixed_line_then_javadoc():
    comment = "// TODO: fix\n/** Real docs. */"
    result = comment_metadata(comment)
    assert result["leading_comment"] == comment
    assert result["javadoc"] == "/** Real docs. */"


def test_mixed_multiline_javadoc():
    comment = "// setup\n// more\n/**\n * Docs.\n */"
    result = comment_metadata(comment)
    assert result["javadoc"] == "/**\n * Docs.\n */"


def test_block_then_javadoc():
    comment = "/* internal */\n/** Public API. */"
    result = comment_metadata(comment)
    assert result["javadoc"] == "/** Public API. */"


def test_javadoc_then_line():
    comment = "/** docs */\n// extra"
    result = comment_metadata(comment)
    assert result["javadoc"] == "/** docs */"


def test_no_boolean_fields():
    result = comment_metadata("// hello")
    assert "has_comment" not in result
    assert "has_javadoc" not in result
