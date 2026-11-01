# Footer and policy customization

The footer is maintained in `pybossa/templates/_footer.html`. Application
templates take precedence over the selected theme. Community and Stats are
removed from Resources, the Projects/Data/Content shortcuts are removed from
Licenses, and the Social section is removed. The underlying routes are still
available; this is a navigation change.

## Policy text

The theme already supports custom policy partials. Keep your institution's text
in the main repository rather than editing the theme submodule:

| Page | Recommended custom file | Existing default content |
| --- | --- | --- |
| Terms of Use | `pybossa/templates/custom/_tos.html` | `pybossa/themes/default/templates/help/_tos.html` |
| Cookies Policy | `pybossa/templates/custom/_cookies_policy.html` | `pybossa/themes/default/templates/help/_cookies_policy.html` |
| Privacy Policy | `pybossa/templates/custom/_privacy_policy.html` | No default body; the theme currently renders only the Privacy Policy heading. |

For a starting point that preserves the current Terms/Cookies content:

```bash
mkdir -p pybossa/templates/custom
cp pybossa/themes/default/templates/help/_tos.html pybossa/templates/custom/_tos.html
cp pybossa/themes/default/templates/help/_cookies_policy.html pybossa/templates/custom/_cookies_policy.html
```

Replace the contents of those copies with your institution's approved text.
Create `_privacy_policy.html` for the Privacy Policy body. These are HTML/Jinja
partials: do not add `{% extends "base.html" %}`. The Terms and Cookies defaults
include their own headings and layout; Privacy already has a page heading, so
its body can use:

```html
<section class="help privacy">
  <div class="container">
    <div class="row">
      <div class="col-md-offset-2 col-md-8">
        <p>Your approved privacy notice goes here.</p>
      </div>
    </div>
  </div>
</section>
```

The page wrappers are `help/tos.html`, `help/cookies_policy.html`, and
`help/privacy.html` in the default theme's templates directory. To change a
wrapper, copy it into the same relative path under `pybossa/templates/`.

Policy wording has not been rewritten by the footer change. The upstream Terms
refer to another organization's privacy notice, the Privacy body is empty, and
the default Cookies page embeds a YouTube video. Review those defaults before
using them as institutional notices.

## Cookie behavior to describe

The current application uses a session cookie, persistent sign-in cookies,
project-password cookies when applicable, project tutorial markers, and the
cookie-banner dismissal preference. The base template supports optional
analytics includes, but this checkout has no analytics include configured.
Custom task presenters may load other services or cookies and need their own
review. The default cookie-policy video loads third-party content.

Keep a clear description of the cookies you use, either on the Cookies Policy
page or within the Privacy Policy. Whether consent is required depends on the
cookie's purpose and applicable rules; a policy page and a consent mechanism
serve different purposes. The existing “Got it” banner is an acknowledgment,
not a mechanism for blocking optional cookies before consent.

## Deploy edits locally

Templates are copied into the Docker image. After saving changes, run from
the repository root:

```bash
docker compose up --build -d
```

Refresh the page when the web container is healthy. Changes do not require a
database migration.
