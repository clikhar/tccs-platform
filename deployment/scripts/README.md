# Controller SIP provisioning

Each TCCS controller has its own SIP identity and its own Asterisk conference namespace:

`TCCS-CTRL-<controller SIP extension>`

For example:

- Controller C01 / SIP `9999` -> `TCCS-CTRL-9999`
- Controller C02 / SIP `9001` -> `TCCS-CTRL-9001`

The browser always calls dialplan extension `900`. Asterisk's `[tccs-controller]`
dialplan uses the browser SIP CallerID to select the controller-specific conference.
A station calling a controller SIP extension (for example `9001`) enters the same
controller-specific conference through `[tccs-stations]`.

## Provision controller SIP endpoints

The controller SIP accounts are stored in PostgreSQL. Asterisk's PJSIP configuration
is generated from the accounts assigned to enabled controllers.

On the Asterisk host, from the repository root:

```bash
chmod +x deployment/scripts/sync_controller_sip.py
python3 deployment/scripts/sync_controller_sip.py
asterisk -rx 'pjsip reload'
```

The script reads `DATABASE_URL`. If it is not set, it uses the local development
PostgreSQL URL used by the backend. For production, use the same database URL as the
backend, for example:

```bash
export DATABASE_URL='postgresql://tccs:YOUR_PASSWORD@127.0.0.1:5432/tccs'
python3 deployment/scripts/sync_controller_sip.py
asterisk -rx 'pjsip reload'
```

The generated file is:

`/etc/asterisk/pjsip.d/tccs-controllers.conf`

Only enabled SIP accounts assigned to enabled controllers are generated. Controller
SIP extensions must be `9xxx`; this keeps them separate from station `10xx` extensions
and matches the station-to-controller dialplan pattern `_9XXX`.

After changing a controller's SIP account, password, assignment, or enabled state in
Controller Management, run the sync script again and reload PJSIP.

## Verify

```bash
asterisk -rx 'pjsip show endpoints'
asterisk -rx 'dialplan show tccs-controller'
asterisk -rx 'dialplan show tccs-stations'
```

A controller using SIP extension `9001` should register as endpoint `9001`. Its browser
conference and any station calls to `9001` must use `TCCS-CTRL-9001`, independently of
other controllers.
