# helpers

What the tests share, imported as `helpers.<module>`, never from
conftest.py or from another test, with the databases each run makes and
the server the browser tests run against. They are the tests' own code,
so the source has no counterpart.
