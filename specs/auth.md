# Auth flows

## Successful login
- goto /login
- type "test@example.com" into the email field
- type "hunter2" into the password field
- click the "Sign in" button
- wait for the dashboard to load
- verify the user dashboard is visible
- verify a greeting with the username is shown
- verify not an error message is shown

## Wrong password is rejected
- goto /login
- type "test@example.com" into the email field
- type "wrong" into the password field
- click the "Sign in" button
- verify an "invalid credentials" error is shown
- verify not the dashboard is visible
