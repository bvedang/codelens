# How to set up SQLITE DB locally for testing things

> [!NOTE]  
> Need to install sqlite3
>
> ```bash
>   brew install sqlite
> ```

- Run Sqlite in terminal first

```bash
sqlite3
```

- Create new db at pwd location of the terminal

```sqlite3
.open --new test.db
```

test.db -> change to whatever you want to name the db

- verify db at current location

```sqlite3
.databases
```

- Quit the Sqlite session

```sqlite3
.quit
```

- Open sqlite with the db you created

```bash
sqlite3 test.db
```

### References

[https://www.prisma.io/dataguide/sqlite/setting-up-a-local-sqlite-database#setting-up-sqlite-on-macos](https://www.prisma.io/dataguide/sqlite/setting-up-a-local-sqlite-database#setting-up-sqlite-on-macos)
