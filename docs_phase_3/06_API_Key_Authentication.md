# API-Key Authentication

## 1. Purpose

Phase 3 introduces API-key authentication for Snip's write operations.

Previously, an unauthenticated client could potentially create or delete links. API-key authentication adds a layer of access control so that only requests presenting a valid API key can perform these operations.

The implementation is designed to:

-   Protect link creation and deletion.
    
-   Generate cryptographically secure API keys.
    
-   Avoid storing raw API keys in PostgreSQL.
    
-   Validate keys before allowing protected operations.
    
-   Keep key generation separate from the public HTTP API.
    
-   Support request rate limiting using the API-key identity.
    

-----------------------


## 2. Authentication Scope

API-key authentication applies to specific endpoints rather than every endpoint.

| Operation | Authentication required |
|-----------|-------------------------|
| `POST /links` | Yes |
| `DELETE /links/{code}` | Yes |
| `GET /links/{code}/stats` | No |
| Redirect requests | No |
| `GET /health` | No |

The protected endpoints require an `X-API-Key` request header.

For example:
```
X-API-Key: snip_your_api_key_here
```

The value above is a placeholder, not a real key.

<br>


This design allows public users to follow short links and retrieve the exposed read-only information without possessing an API key, while restricting link creation and deletion.

Authentication and authorization are related but distinct concepts. Authentication establishes whether the supplied key is valid. The endpoint's access-control rules determine which operations require that authentication.


---------------

## 3. API-Key Generation

Snip generates API keys through a dedicated command-line utility rather than a public HTTP endpoint.

The key-generation process uses Python's `secrets` module to produce cryptographically secure random material. Keys use the `snip_` prefix followed by a URL-safe random token.
<br>

The recorded implementation uses `secrets.token_urlsafe(32)`.

The prefix makes the key recognizable as a Snip credential. It is not itself a security mechanism; the random token provides the unpredictability.
<br>

Keys are generated through the project's Makefile workflow:

```
make key n="laptop"
```

**The name is a label identifying the key. The command invokes the one-off key-generation utility in the application environment.**

**There is no public HTTP endpoint for minting new API keys.**

-----------


## 4. Why Raw Keys Are Not Stored

Storing raw API keys in the database would allow anyone who obtained a database dump to recover usable credentials directly.
<br>

Instead, Snip stores a SHA-256 hash of the key.

Conceptually:
```
[  
\text{Stored value} = \operatorname{SHA256}(\text{API key})  
]
```

When a client supplies a key, the application hashes the supplied value and uses the hash for validation against the stored record.

This means the database stores a verification value rather than the original credential.

API keys should have high entropy, as Snip's generated keys do. Hashing does not make a weak, predictable key safe, and a stolen raw key remains usable until it is revoked.

The raw key is displayed when it is generated. It should be captured securely at that point because the database does not retain the original plaintext value for later retrieval.


--------


## 5. Request Authentication Flow

For a protected endpoint, the authentication process follows this general sequence:

1.  The client sends a request with the `X-API-Key` header.
    
2.  The application checks whether the key is present.
    
3.  The supplied key is hashed.
    
4.  The application looks up the corresponding stored key record.
    
5.  If the key is missing or invalid, the request is rejected.
    
6.  If the key is valid, the protected operation can proceed, subject to other applicable controls such as rate limiting.

<br>


The intended request outcomes are:

| Request | Expected result |
|---------|-----------------|
| No API key | `401 Unauthorized` |
| Invalid API key | `401 Unauthorized` |
| Valid API key for link creation | Request may proceed; successful creation returns `201 Created` |
| Valid API key for deletion | Request may proceed, subject to the endpoint's normal rules |

<br>


Authentication does not guarantee that every valid request succeeds. The requested resource may not exist, the request may be invalid, or another application-level error may occur.


------------------

## 6. API-Key Storage and Identity

The stored API-key record associates a key hash with its identifying metadata, including the key's name.

The raw API key is not used as the database's stored verification value.

The hash also provides a stable identifier for the rate-limiting mechanism. 

This lets the application associate requests with a key without using the raw credential as the Redis rate-limit key.
<br>

Conceptually:

```
Raw API key
     |
     v
SHA-256 hash
     |
     +----> Validate against stored key record
     |
     +----> Identify rate-limit bucket
```

The hash is not equivalent to encryption. There is no decryption step to recover the original key from the stored hash.



--------------------

## 7. Security Considerations

### Keep API keys secret

Treat an API key like a password or bearer credential. Anyone possessing a valid key may be able to perform the operations allowed to that key.

Do not commit real keys to Git, include them in screenshots, or publish them in documentation.

<br>

### Use HTTPS outside local development

API keys sent over an unencrypted connection can be intercepted. Deployments exposed to clients should use HTTPS and protect credentials in transit.
<br>

### Avoid logging raw credentials

Application logs should not expose the `X-API-Key` value. Logging should be designed so authentication failures can be investigated without recording usable secrets.
<br>

### Use least privilege

The current implementation applies a shared API-key requirement to the protected write operations. It does not establish a fine-grained role system for different key permissions.

Keys should be issued only to clients that need write access.
<br>

### Remember that authentication is not a complete abuse-prevention system

A valid API key identifies a credential, but it does not prove that every request made with that credential is legitimate. Rate limiting and other operational controls help reduce misuse.


------------------------------------

## 8. Revocation and Rotation

The recorded Phase 3 implementation does not provide a dedicated key rotation or revocation workflow.

**Deleting the corresponding stored key record manually revokes that key because subsequent validation can no longer find the stored hash.**

**This is a basic revocation mechanism, but it is not a complete key-management lifecycle.**

A more developed implementation could provide:

-   Explicit key revocation.
    
-   Key rotation with a transition period.
    
-   Creation and last-used timestamps.
    
-   Expiration policies.
    
-   Auditing of key-management actions.
    
-   Different permissions for different API keys.
    

Until those features exist, key revocation should be handled carefully through the database and an established operational procedure.


------

## 9. Verification Performed

The recorded Phase 3 regression test checked link creation with and without an API key.

The results were:


| Test | Result |
|------|--------|
| Create link without an API key | `401 Unauthorized` |
| Create link with a valid API key | `201 Created` |

<br>


These results demonstrate that the tested link-creation endpoint rejects a missing key and accepts a valid one.

They do not independently prove every possible authentication failure case, secure transport configuration, or the absence of credential leakage from every logging path.



------------


## 10. Troubleshooting

### A valid key returns `401 Unauthorized`

Check that:

-   The request sends the key in the `X-API-Key` header.
    
-   The key was copied correctly when generated.
    
-   The corresponding key record still exists.
    
-   The application's database connection and key lookup are working.
    
-   The client is using the intended application environment.
    

Do not place the real key in public logs or paste it into shared troubleshooting notes.
<br>

### Link creation works locally but fails in another environment

Verify that the target environment has the appropriate API-key record. A key generated against one database is not automatically registered in another independent database.
<br>

### A key may have been exposed

Treat the key as compromised. Revoke its database record, generate a replacement, update the authorized client, and confirm that the old key no longer authenticates.

If the key was committed to Git, removing it from the latest file is not sufficient by itself: the secret may remain in repository history or other copies.


-------------------------

## 11. Key Takeaways

-   `POST /links` and `DELETE /links/{code}` require `X-API-Key`.
    
-   Public redirects, statistics, and the health endpoint remain unauthenticated.
    
-   API keys are generated using Python's `secrets` module.
    
-   The application stores a SHA-256 hash rather than the raw key.
    
-   Key generation uses `make key n="laptop"` as the documented workflow.
    
-   Missing or invalid keys are rejected with `401 Unauthorized`.
    
-   Redis rate limiting uses the key hash as part of its identity.
    
-   Manual deletion of a stored key record revokes that key, but automated rotation and revocation workflows remain future improvements.
