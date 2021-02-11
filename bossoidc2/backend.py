# Copyright 2016 The Johns Hopkins University Applied Physics Laboratory
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.settings import import_from_string
from rest_framework.authentication import get_authorization_header

from django.utils.encoding import force_bytes, smart_text, smart_bytes
from django.utils.translation import ugettext as _
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from bossoidc2.models import Keycloak as KeycloakModel
from jwkest.jwt import JWT
from josepy.jws import JWS, Header

import requests
import json
import logging

def _log(child):
    return logging.getLogger(__name__).getChild(child)

def load_user_roles(user, roles):
    """Default implementation of the LOAD_USER_ROLES callback

    Args:
        user (UserModel): Django user object for the user logging in
        roles (list[str]): List of Keycloak roles assigned to the user
                           Note: Contains both realm roles and client roles
    """
    pass

LOAD_USER_ROLES = getattr(settings, 'LOAD_USER_ROLES', None)
if LOAD_USER_ROLES is None:
    # DP NOTE: had issues with import_from_string loading bossoidc.backend.load_user_roles
    LOAD_USER_ROLES_FUNCTION = load_user_roles
else: # pragma: no cover
    LOAD_USER_ROLES_FUNCTION = import_from_string(LOAD_USER_ROLES, 'LOAD_USER_ROLES')

KEYCLOAK_ADMIN_USER = getattr(settings, 'KEYCLOAK_ADMIN_USER', 'bossadmin')


def update_user_data(user, userinfo):
    """Default implementation of the UPDATE_USER_DATA callback

    Args:
        user (UserModel): Django user object for the user logging in
        userinfo (dict): Dictionary of userinfo requested from Keycloak with the
                         user's profile data
    """
    pass

UPDATE_USER_DATA = getattr(settings, 'UPDATE_USER_DATA', None)
if UPDATE_USER_DATA is None:
    UPDATE_USER_DATA_FUNCTION = update_user_data
else: # pragma: no cover
    UPDATE_USER_DATA_FUNCTION = import_from_string(UPDATE_USER_DATA, 'UPDATE_USER_DATA')


def check_username(username):
    """Ensure that the given username does exceed the current user models field
    length

    Args:
        username (str): Username of the user logging in

    Raises:
        AuthenticationFailed: If the username length exceeds the fields max length
    """
    username_field = get_user_model()._meta.get_field("username")
    if len(username) > username_field.max_length:
        raise AuthenticationFailed(_('Username is too long for Django'))


def get_user_by_id(request, userinfo):
    """Get or create the user object based on the user's information

    Note: Taken from djangooidc.backends.OpenIdConnectBackend and made common for
    drf-oidc-auth to make use of the same create user functionality

    Note: The user's token is loaded from the request session or header to load_user_roles
    the user's Keycloak roles

    Args:
        request (Request): Django request from the user
        userinfo (dict): Dictionary of userinfo requested from Keycloak with the
                         user's profile data

    Returns:
        UserModel: user object for the requesting user
        None: If the requesting user's token's audience is not valid

    Raises:
        AuthenticationFailed: If the requesting user's username is too long
    """

    access_token = get_access_token(request)
    audience = get_token_audience(access_token)
    if not token_audience_is_valid(audience):
        return None
    return get_user_with_id(access_token, userinfo)

def get_user_with_id(access_token, userinfo):
    """Common functionality for getting or creating the user.  Used by both
    mozilla_django_oidc and drf-oidc-auth.

    Args:
        access_token ():
        userinfo (dict): Dictionary of userinfo requested from Keycloak with the

    Returns:
        UserModel: user object for the requesting user
        None: If the requesting user's token's audience is not valid

    Raises:
        AuthenticationFailed: If the requesting user's username is too long
    """
    UserModel = get_user_model()
    uid = userinfo['sub']
    username = userinfo['preferred_username']

    check_username(username)

    # Some OP may actually choose to withhold some information, so we must test if it is present
    openid_data = {'last_login': datetime.datetime.now()}
    if 'first_name' in userinfo.keys():
        openid_data['first_name'] = userinfo['first_name']
    if 'given_name' in userinfo.keys():
        openid_data['first_name'] = userinfo['given_name']
    if 'christian_name' in userinfo.keys():
        openid_data['first_name'] = userinfo['christian_name']
    if 'family_name' in userinfo.keys():
        openid_data['last_name'] = userinfo['family_name']
    if 'last_name' in userinfo.keys():
        openid_data['last_name'] = userinfo['last_name']
    if 'email' in userinfo.keys():
        openid_data['email'] = userinfo['email']

    # DP NOTE: The thing that we are trying to prevent is the user account being
    #          deleted and recreated in Keycloak (all user data the same, but a
    #          different uid) and getting the application permissions of the old
    #          user account.

    try: # try to lookup by keycloak UID first
        kc_user = KeycloakModel.objects.get(UID = uid)
        user = kc_user.user
    except KeycloakModel.DoesNotExist: # user doesn't exist with a keycloak UID
        try:
            user = UserModel.objects.get_by_natural_key(username)

            fmt = "Deleting user '{}' because it matches the authenticated Keycloak username"
            _log('get_user_by_id').info(fmt.format(username))

            # remove existing user account, so permissions are not transfered
            # DP NOTE: required, as the username field is still a unique field,
            #          which doesn't allow multiple users in the table with the
            #          same username
            user.delete()
        except UserModel.DoesNotExist:
            pass

        args = {UserModel.USERNAME_FIELD: username, 'defaults': openid_data, }
        user, created = UserModel.objects.update_or_create(**args)
        kc_user = KeycloakModel.objects.create(user = user, UID = uid)

    roles = get_roles(access_token)
    user.is_staff = 'admin' in roles or 'superuser' in roles
    user.is_superuser = 'superuser' in roles

    LOAD_USER_ROLES_FUNCTION(user, roles)
    UPDATE_USER_DATA_FUNCTION(user, userinfo)

    user.save()
    return user


def get_roles(decoded_token):
    """Get roles declared in the input token

    Note: returns both the realm roles and client roles

    Args:
        decoded_token (dict): The user's decoded bearer token

    Returns:
        list[str]: List of role names
    """

    # Extract realm scoped roles
    try:
        # Session logins and Bearer tokens from password Grant Types
        if 'realm_access' in decoded_token:
            roles = decoded_token['realm_access']['roles']
        else: #  Bearer tokens from authorization_code Grant Types
              # DP ???: a session login uses an authorization_code code, not sure
              #         about the difference
            roles = decoded_token['resource_access']['account']['roles']
    except KeyError:
        roles = []

    # Extract all client scoped roles
    for name, client in decoded_token.get('resource_access', {}).items():
        if name is 'account':
            continue

        try:
            roles.extend(client['roles'])
        except KeyError: # pragma no cover
            pass

    return roles


def get_access_token(request):
    """Retrieve access token from the request

    The access token is searched first the request's session. If it is not
    found it is then searched in the request's ``Authorization`` header.

    Args:
        request (Request): Django request from the user

    Returns:
        dict: JWT payload of the bearer token
    """
    access_token = request.session.get("access_token")
    if access_token is None:  # Bearer token login
        access_token = get_authorization_header(request).split()[1]
        if not access_token:
            access_token = 'public'
    return JWT().unpack(access_token).payload()


def get_token_audience(token):
    """Retrieve the token's intended audience

    According to the openid-connect spec `aud` may be a string or a list:
        http://openid.net/specs/openid-connect-basic-1_0.html#IDToken

    Args:
        token (dict): The user's decoded bearer token

    Returns:
        list[str]: The list of token audiences
    """

    aud = token.get("aud", [])
    return [aud] if isinstance(aud, str) else aud


def token_audience_is_valid(audience):
    """Check if the input audiences is valid

    Args:
        audience (list[str]): List of token audiences

    Returns:
        bool: If any of the audience is in the list of requested audiences
    """

    if not hasattr(settings, 'OIDC_AUTH'):
        # Don't assume that the bossoidc settings module was used
        return False

    trusted_audiences = settings.OIDC_AUTH.get('OIDC_AUDIENCES', [])

    for aud in audience:
        if aud in trusted_audiences:
            result = True
            break
    else:
        result = False
    return result


class OpenIdConnectBackend(OIDCAuthenticationBackend): # pragma: no cover
    """Subclass of the Django OIDC Backend that makes use of our get_user_by_id
    implementation
    """

    def retrieve_matching_jwk(self, token):
        """Get the signing key by exploring the JWKS endpoint of the OP.

        Overriding original method because KeyCloak isn't providing the key id
        in the token.  Since there's only one jwks key, try using that key
        to verify the token's signature.

        Args:
            token

        Returns:
            Public signing key for the KeyCloak realm.

        Raises:
            (SuspiciousOperation)
        """
        response_jwks = requests.get(
            self.OIDC_OP_JWKS_ENDPOINT,
            verify=self.get_settings('OIDC_VERIFY_SSL', True)
        )
        response_jwks.raise_for_status()
        jwks = response_jwks.json()

        # Compute the current header from the given token to find a match
        jws = JWS.from_compact(token)
        json_header = jws.signature.protected
        header = Header.json_loads(json_header)

        key = None
        num_keys = len(jwks['keys'])
        for jwk in jwks['keys']:
            # If there's only one key, then try it even if the key id doesn't
            # match or is None.
            if jwk['kid'] != smart_text(header.kid) and num_keys > 1:
                continue
            if 'alg' in jwk and jwk['alg'] != smart_text(header.alg):
                raise SuspiciousOperation('alg values do not match.')
            key = jwk
        if key is None:
            raise SuspiciousOperation('Could not find a valid JWKS.')
        return key

    def verify_claims(self, claims):
        """Ensure required claims provided.

        Claims are specified by the OIDC_RP_SCOPES variable in the Django
        settings.

        Args:
            claims (list[str]): Claims provided.

        Returns:
            (boolean)
        """
        scopes = self.get_settings('OIDC_CLAIMS_VERIFICATION', 'preferred_username sub').split()

        for field in scopes:
            if field == 'email':
                # We don't provide an email address when we create bossadmin,
                # so don't require email if this is the bossadmin user.
                if 'preferred_username' in claims and claims['preferred_username'] == KEYCLOAK_ADMIN_USER:
                    continue
            if field not in claims:
                return False

        return True

    def get_or_create_user(self, access_token, id_token, payload):
        """Get or create the user based on the provided arguments.

        Args:
            access_token
            id_token
            payload

        Returns:
            User info.

        Raises:
            (SuspiciousOperation): When not all required claims provided.
        """
        user_info = self.get_userinfo(access_token, id_token, payload)
        claims_verified = self.verify_claims(user_info)
        if not claims_verified:
            msg = 'Claims verification failed'
            raise SuspiciousOperation(msg)

        decoded_token = JWT().unpack(access_token).payload()
        user = get_user_with_id(decoded_token, user_info)
        return user
