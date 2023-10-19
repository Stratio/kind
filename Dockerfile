FROM stratio/cloud-testing-suite:0.1.0-SNAPSHOT

RUN chmod -R 0700 /CTS

VOLUME /var/lib/docker

COPY bin/cloud-provisioner.tar.gz /CTS/resources/bin/cloud-provisioner

CMD ["bash"]