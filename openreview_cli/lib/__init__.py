"""Library modules of the OpenReview CLI.

Split out from the CLI layer so that the status / review logic can be
reused (and tested) without any I/O side effects: nothing under this
package prints.
"""
