import graphene
from graphene import relay

from ..mail import send_offer_accepted, send_request_accepted
from ..models import (
    Offer,
    Enrollment,
    Student,
    StudentRequest,
)


class Accepting:
    @staticmethod
    def freq_conflicting(a, b):
        return bool({1, 4} & {a, b}) or a == b

    @staticmethod
    def times_conflicting(a, b):
        return (a.start < b.end and a.end > b.start) or (
            b.start < a.end and b.end > a.start
        )

    @staticmethod
    def conflicting(time, exchange_to):
        if exchange_to.count() != 1:
            return True

        def class_time_cmp(a, b):
            return bool(
                a.day == b.day
                and Accepting.freq_conflicting(a.frequency, b.frequency)
                and Accepting.times_conflicting(a, b)
            )

        return class_time_cmp(exchange_to.all()[0], time)

    @staticmethod
    def user_has_class_time_to_trade(user_classes_to_trade, offer_exchange_to):
        if offer_exchange_to:
            return bool(set(user_classes_to_trade) & set(offer_exchange_to))

        # To allow offers with *any* exchange_to
        return bool(user_classes_to_trade)

    @staticmethod
    def user_has_collision(user_classes, user_classes_to_trade, offer_time):
        for user_class in set(user_classes) - set(user_classes_to_trade):
            if Accepting.conflicting(user_class, offer_time):
                return True
        return False
        # return bool((set(user_classes) - set(user_classes_to_trade)) & {offer_time})

    @staticmethod
    def test_user(user):
        return user.is_authenticated

    @staticmethod
    def test_active(obj):
        return obj.active

    @staticmethod
    def get_id(fake_id):
        return relay.Node.from_global_id(global_id=fake_id)[1]

    @staticmethod
    def test_exchange(user_to_trade, user_class_times, offer):
        return Accepting.user_has_class_time_to_trade(
            user_to_trade, offer.exchange_to.all()
        ) and not Accepting.user_has_collision(
            user_class_times, user_to_trade, offer.enrollment.class_time
        )

    @staticmethod
    def test_request(request, student):
        for time in list(
            map(lambda x: x.class_time, Enrollment.objects.filter(student=student))
        ):
            if Accepting.conflicting(time, request.exchange_to):
                return False
        return True

    @staticmethod
    def clean_offers(student, course, class_time):
        try:
            user_offer = Offer.objects.get(
                enrollment__student=student,
                enrollment__class_time__course=course,
            )
            user_offer.active = False
            user_offer.save(force_update=True)
        except Offer.DoesNotExist as ignored:
            pass
        try:
            user_request = StudentRequest.objects.get(
                enrollment__student=student,
                enrollment__class_time__coursee=course,
            )
            user_request.active = False
            user_request.save(force_update=True)
        except StudentRequest.DoesNotExist as ignored:
            pass
        offers = list(Offer.objects.filter(enrollment__student=student))
        for offer in offers:
            for exchange in offer.exchange_to:
                if Accepting.conflicting(exchange, class_time):
                    offer.remove(exchange)
                    offer.save()
                    if len(offer.exchange_to) == 0:
                        offer.active = False
                        offer.save()
        requests = list(StudentRequest.objects.filter(enrollment__student=student))
        for request in requests:
            for exchange in request.exchange_to:
                if Accepting.conflicting(exchange, class_time):
                    request.remove(exchange)
                    request.save()
                    if len(request.exchange_to) == 0:
                        request.active = False
                        request.save()


class AcceptOffer(Accepting, graphene.Mutation):
    class Arguments:
        offer_id = graphene.String()

    accepted = graphene.Boolean()

    @staticmethod
    def mutate(root, info, offer_id):
        if not Accepting.test_user(
            user := info.context.user
        ) or not Accepting.test_active(
            offer := Offer.objects.get(id=Accepting.get_id(offer_id))
        ):
            return AcceptOffer(accepted=False)
        student = Student.objects.get(account=user)
        user_enrollments = list(Enrollment.objects.filter(student=student))
        user_class_times = [e.class_time for e in user_enrollments]
        course = offer.enrollment.class_time.course
        user_to_trade = list(
            filter(
                lambda x: x.course == course,
                user_class_times,
            )
        )
        if not Accepting.test_exchange(user_to_trade, user_class_times, offer):
            return AcceptOffer(accepted=False)

        offer.active = False
        offer.save()
        user_enrollment = next(
            filter(
                lambda x: x.class_time.course == course,
                user_enrollments,
            )
        )
        Accepting.clean_offers(student, course, offer.enrollment.class_time)
        offer.enrollment.student, user_enrollment.student = (
            user_enrollment.student,
            offer.enrollment.student,
        )
        offer.enrollment.save()
        user_enrollment.save()
        offer.save()
        send_offer_accepted(
            offer_client_mail=user_enrollment.student.account.email,
            offer_owner_mail=offer.enrollment.student.account.email,
            client_class_time=user_enrollment.class_time,
            offer_class_time=offer.enrollment.class_time,
        )
        return AcceptOffer(accepted=True)


class AcceptRequest(Accepting, graphene.Mutation):
    class Arguments:
        offer_id = graphene.String()

    accepted = graphene.Boolean()

    @staticmethod
    def mutate(root, info, offer_id):
        request_id = offer_id
        request = StudentRequest.objects.get(id=Accepting.get_id(request_id))
        student = request.enrollment.student
        if (
            not Accepting.test_user(user := info.context.user)
            or not Accepting.test_active(request)
            or not request.lecturer.account == user
            or not Accepting.test_request(request, student)
        ):
            return AcceptOffer(accepted=False)

        request.active = False
        request.enrollment.class_time = request.exchange_to.first()
        request.enrollment.save()
        request.save()
        Accepting.clean_offers(
            student, request.enrollment.class_time.course, request.enrollment.class_time
        )
        send_request_accepted(
            student_mail=student.account.email,
            lecturer_mail=user.email,
            class_time=request.exchange_to.first(),
            former_class_time=request.enrollment.class_time,
            comment=request.comment,
            name=student,
        )
        return AcceptRequest(accepted=True)
