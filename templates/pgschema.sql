{# Default schema for postgresql image #}
alter user postgres with encrypted password '{{ POSTGRES }}';
create user repuser replication login connection limit 1 encrypted password 'repuser';
