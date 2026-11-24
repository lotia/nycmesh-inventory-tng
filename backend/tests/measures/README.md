# measures

Measurements, not tests: each fills a database of its own with a store's
worth of rows and prints how long pages take and their queries' plans.
pytest collects one only when named, as the mise task that runs it does
(its first lines name the task). They measure the running app, not one
package of it.
