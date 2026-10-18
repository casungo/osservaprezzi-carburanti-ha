#!/usr/bin/env bash
# Report public upstream connectivity after a failed CI check.
set -u

for endpoint in \
  https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv \
  https://carburanti.mise.gov.it/ospzApi/registry/servicearea/54233
do
  host="${endpoint#https://}"
  host="${host%%/*}"
  printf '\nDNS for %s\n' "$host"
  if ! timeout 15 getent ahosts "$host"; then
    printf 'DNS lookup failed or timed out\n'
  fi
  for family in -4 -6; do
    printf '\nHTTPS %s %s\n' "$family" "$endpoint"
    if curl "$family" --silent --show-error --location \
      --connect-timeout 10 --max-time 20 --output /dev/null \
      --write-out 'HTTP %{http_code}; remote %{remote_ip}; DNS %{time_namelookup}s; connect %{time_connect}s; TLS %{time_appconnect}s; total %{time_total}s\n' \
      "$endpoint"; then
      printf 'curl exit 0\n'
    else
      printf 'curl exit %s\n' "$?"
    fi
  done
done
