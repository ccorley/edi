"""
eligibilitycheck.py

Extends the EDI processing workflow to add a simple FHIR R4 translation.
"""
import datetime
import json
import logging
import ssl

from asyncio import get_running_loop
from edi.core.models import EdiOperations
from edi.core.support import perf_counter_ms
from edi.core.workflows import EdiProcessor
from edi.extensions.x12 import X12SegmentReader
from nats.aio.client import Client as NatsClient, Msg
from x12.io import X12ModelReader
from x12.encoding import X12JsonEncoder
from typing import Any
from xworkflows import transition


logger = logging.getLogger(__name__)


class EdiEligibilityCheckProcessor(EdiProcessor):
    """
    Translates EDI messages to FHIR R4 for a 270/271 eligibility check example workflow.
    """

    def __init__(self, input_message: Any):
        """
        Configures the EdiEligibilityCheckProcessor instance.
        """
        self.subscriber_last = None
        self.subscriber_first = None
        self.subscriber_id = None
        self.insurer_name = None
        self.insurer_id = None
        self.provider_name = None
        self.provider_id = None
        self.transaction_id = None
        self.nats_client = None
        super(EdiEligibilityCheckProcessor, self).__init__(input_message)

    @transition("translate")
    async def translate(self):
        """
        Translate the input message to FHIR R4.
        """
        start = perf_counter_ms()
        logger.info("In translate()")

        # get data from the incoming 270 for a FHIR R4 CoverageEligibilityRequest
        with X12ModelReader(self.input_message) as r:
            models = []
            for m in r.models():
                model_data = m.dict(exclude_unset=False,
                                    exclude_none=False)
                models.append(model_data)

        # iterate over segment models to find fields
        for model in models:
            json_opts = {"cls": X12JsonEncoder, "indent": 4}
            logger.debug(f"{json.dumps(model, **json_opts)}")

            segment = model["loop_2000a"][0]["loop_2000b"][0]["loop_2000c"][0]["loop_2100c"]["nm1_segment"]
            self.subscriber_last = segment["name_last_or_organization_name"]
            self.subscriber_first = segment["name_first"]
            self.subscriber_id = segment["identification_code"]
            segment = model["loop_2000a"][0]["loop_2000b"][0]["loop_2000c"][0]["trn_segment"][0]
            self.transaction_id = segment["originating_company_identifier"]
            segment = model["loop_2000a"][0]["loop_2100a"]["nm1_segment"]
            self.insurer_name = segment["name_last_or_organization_name"]
            self.insurer_id = segment["identification_code"]
            segment = model["loop_2000a"][0]["loop_2000b"][0]["loop_2100b"]["nm1_segment"]
            self.provider_name = segment["name_last_or_organization_name"]
            self.provider_id = segment["identification_code"]

            self.transmit_data = self._create_request()

            end = perf_counter_ms()
            elapsed_time = end - start
            self.metrics.translateTime = elapsed_time
            self.operations.append(EdiOperations.TRANSLATE)

    def _create_request(self) -> dict:
        """
        Get the simulated FHIR references for the patient and return a CoverageEligibilityRequest resource
        containing the patient's information.

        :return: dict for FHIR CoverageEligibilityRequest
        """
        patient_ref = self._get_patient_reference(self.subscriber_last, self.subscriber_first, self.subscriber_id)
        insurer_ref = self._get_insurer_reference(self.insurer_name, self.insurer_id)
        coverage_ref = self._get_coverage_reference(self.subscriber_last, self.subscriber_first, self.subscriber_id)
        today = datetime.date.today()

        resource = {
            "resourceType": "CoverageEligibilityRequest",
            "id": self.transaction_id,
            "text": {
                "status": "generated",
                "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">A human-readable rendering of the CoverageEligibilityRequest</div>"
            },
            "identifier": [
                {
                    "system": "http://happyvalley.com/coverageelegibilityrequest",
                    "value": self.transaction_id
                }
            ],
            "status": "active",
            "priority": {
                "coding": [
                    {
                        "code": "normal"
                    }
                ]
            },
            "purpose": [
                "validation"
            ],
            "patient": {
                "reference": patient_ref
            },
            "created": today.isoformat(),
            "provider": {
                "reference": "Organization/1"
            },
            "insurer": {
                "reference": insurer_ref
            },
            "insurance": [
                {
                    "focal": True,
                    "coverage": {
                        "reference": coverage_ref
                    }
                }
            ]
        }

        logger.info(f"CoverageEligibilityRequest = {resource}")
        return resource

    def _get_patient_reference(self, last: str, first: str, _id: str) -> str:
        """
        Simulate a lookup of the FHIR reference for the patient.  Extend as needed to support more patients.

        :return: string representing the FHIR Patient reference
        """
        if last == "DOE" and first == "JOHN" and _id == "11122333301":
            return "Patient/001"

    def _get_insurer_reference(self, name: str, _id: str) -> str:
        """
        Simulate a lookup of the FHIR reference for the insurer organization.  Extend as needed to support more orgs.

        :return: string representing the FHIR Organization reference
        """
        if name == "UNIFIED INSURANCE CO" and _id == "842610001":
            return "Organization/001"

    def _get_coverage_reference(self, last: str, first: str, _id: str) -> str:
        """
        Simulate a lookup of the FHIR reference for the coverage resource.  Extend as needed to support more resources.

        :return: string representing the FHIR Coverage reference for the patient
        """
        if last == "DOE" and first == "JOHN" and _id == "11122333301":
            return "Coverage/9876B1"

    async def start_nats_coverage_eligibility_subscriber(self) -> None:
        """
        Start the NATS subscriber for eligibility.EVENTS messages.
        """
        ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ssl_context.load_verify_locations(cafile="./local-config/nats/lfh-root-ca.pem")
        self.nats_client = NatsClient()
        await self.nats_client.connect(
            servers="tls://localhost:4222",
            nkeys_seed="./local-config/nats/nats-server.nk",
            loop=get_running_loop(),
            tls=ssl_context,
            allow_reconnect=True,
            max_reconnect_attempts=10,
        )
        # consume the NATS JetStream Consumer
        await self.nats_client.subscribe("eligibility.EVENTS", cb=self.nats_coverage_response_callback)

    async def nats_coverage_response_callback(self, msg: Msg) -> None:
        """
        Callback for NATS JetStream 'eligibility.EVENTS' messages.  When a message is received, create a
        simulated X12 271 response, based on the original X12 270 eligibility request and the eligibility decision
        returned from the blockchain smart contract.

        :param msg: a message delivered from the NATS server
        """
        subject = msg.subject
        reply = msg.reply
        data = msg.data.decode()
        print(f"nats_coverage_response_callback: received a message on {subject} {reply}")

        message = json.loads(data)
        json_opts = {"cls": X12JsonEncoder, "indent": 4}
        print(f"{json.dumps(message, **json_opts)}")

        # check the transaction id
        if message["identifier"][0]["value"] != self.transaction_id:
            print(f"Not the message id {self.transaction_id} we are looking for - " +
                  f"actual id = {message['identifier'][0]['value']}")
            return

        # get the eligibility result
        eligibility = 6
        if message["insurance"][0]["inforce"]:
            eligibility = 1

        # get the 271 template and parse
        with open("./samples/271.x12") as f:
            template = ",".join(f.readlines())

        # parse the 271 template and populate with results
        output_message = ""
        with X12SegmentReader(template) as r:
            for segment in r.segments():
                logger.debug(f"Segment = {segment}")
                # get the element delimiter
                if segment[0:3] == "ISA":
                    element_delimiter = segment[3:4]
                else:
                    output_message += "~"

                    # get the elements
                elements = r.elements(segment, element_delimiter)
                for element in elements:
                    logger.debug(f"Element = {element}")

                # set the info in the 271 template
                if segment[0:3] == "NM1" and elements[1] == "IL":
                    elements[3] = self.subscriber_last
                    elements[4] = self.subscriber_first
                    elements[9] = self.subscriber_id
                elif segment[0:3] == "NM1" and elements[1] == "PR":
                    elements[3] = self.insurer_name
                    elements[9] = self.insurer_id
                elif segment[0:3] == "NM1" and elements[1] == "1P":
                    elements[3] = self.provider_name
                    elements[9] = self.provider_id
                elif segment[0:3] == "TRN":
                    elements[2] = self.transaction_id
                elif segment[0:2] == "EB":
                    elements[1] = str(eligibility)

                output_message += element_delimiter.join(elements).rstrip(element_delimiter)

        print(f"271 result: {output_message}")
        self.message_received = True

    async def stop_nats_coverage_eligibility_subscriber(self):
        await self.nats_client.close()
