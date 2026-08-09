ADMINISTRATOR_ROLE = 'administrator'
CONTRIBUTOR_ROLE = 'contributor'


def get_site_role(user):
    """Return the public editorial role derived from Django admin access."""
    if not user:
        return None
    if user.is_superuser:
        return ADMINISTRATOR_ROLE
    if user.is_staff:
        return CONTRIBUTOR_ROLE
    return None
