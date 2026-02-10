FROM quay.io/keycloak/keycloak:26.0

# Install p2-inc/keycloak-events extension for webhook support
ADD --chmod=644 https://repo1.maven.org/maven2/io/phasetwo/keycloak/keycloak-events/0.35/keycloak-events-0.35.jar /opt/keycloak/providers/

# Keycloak auto-discovers providers in /opt/keycloak/providers/ on startup
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
